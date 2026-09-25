package jp.sleeptree.fragment.call

import android.annotation.SuppressLint
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.telecom.DisconnectCause
import android.telecom.PhoneAccount
import android.telecom.TelecomManager
import android.util.Log
import jp.sleeptree.fragment.Prefs
import jp.sleeptree.fragment.ServerState
import jp.sleeptree.fragment.api.FragmentApi
import jp.sleeptree.fragment.fragmentPhoneAccountHandle
import jp.sleeptree.fragment.ui.IncomingCallActivity
import jp.sleeptree.fragment.ui.MainActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * 取り次ぎの呼び出しを1本だけ抱える。
 *
 * ⚠プロセスに1つで足りる。フラグメントの取り次ぎは「いま応対中の1本の通話に本人を呼ぶ」ものなので、
 *   同時に2本鳴ることが構造上ない (hookdのHANDOFFもルーム単位で1つ)。
 */
object CallCoordinator {

    /** 鳴らす理由。⚠出たあとの振る舞いが根本的に違う (下の answer を参照) */
    enum class Kind {
        /** AIが応対中の通話に本人を呼び込む (取り次ぎ) */
        HANDOFF,

        /** まだAIが出ていない着信を本人が先に取る (スタンバイの2秒鳴動 / 自分で出る) */
        CALL,
    }

    data class Incoming(
        /** ⚠CALL のときは空。ルームはまだ存在しない (本人が出てから Asterisk がブリッジする) */
        val room: String,
        val number: String,
        val name: String?,
        val reason: String,
        /** 判断材料。⚠【本人限定】を含みうるのでロック中は描画しない (IncomingCallActivity側で制御) */
        val fragments: List<FragmentApi.Fragment> = emptyList(),
        /** 直近の会話。鳴っている間も伸びる */
        val recent: List<FragmentApi.Segment> = emptyList(),
        val kind: Kind = Kind.HANDOFF,
        /** 電話帳に無い相手の番号検索。null = 電話帳にある / 検索していない */
        val lookup: FragmentApi.Lookup? = null,
        /**
         * 録音告知中 (2026-09-25)。画面は出すが、出るボタンと着信音はまだ出さない。
         * ⚠サーバーも告知中の受話は断る (hookd が 409)。OS の応答ボタン (ヘッドセット等) も
         *   answer() で弾く
         */
        val announcing: Boolean = false,
    )

    private val _incoming = MutableStateFlow<Incoming?>(null)
    val incoming: StateFlow<Incoming?> = _incoming

    /** Telecom が作った Connection。応答・切断を伝えるために持っておく */
    @Volatile
    internal var connection: FragmentConnection? = null

    private val scope = CoroutineScope(Dispatchers.IO)

    /**
     * 本人が「出る」を押した通話か (2026-09-19)。true の間は「AI応対中の表示」(LiveCallActivity) を
     * 出さない — 管制室 (MainActivity) が既にロック画面の上に出ていて、その上に被せると邪魔
     */
    @Volatile
    var answeredByUser: Boolean = false

    /**
     * hookd が「本人を呼べ」と言ってきた。Telecom に着信を登録する。
     *
     * ⚠自己管理型なので `addNewIncomingCall` を呼んでも**OSは着信画面を出さない**。
     *   OSがやるのは音声ルーティングと他通話との調停だけで、UIとベル鳴らしはこちらの責任
     *   (FragmentConnection.onShowIncomingCallUi)。
     */
    @SuppressLint("MissingPermission")
    fun onHandoffRequested(context: Context, h: FragmentApi.Handoff) {
        ring(
            context,
            Incoming(h.room, h.number, h.name, h.reason, h.fragments, h.recent, Kind.HANDOFF, h.lookup),
        )
    }

    /**
     * まだAIが出ていない着信 (スタンバイの2秒鳴動 / 自分で出る)。
     *
     * ⚠取り次ぎと違って**判断材料が無い**。相手はまだ何も喋っていないので、
     *   出せるのは番号と電話帳の名前だけ。下調べの結果を待つ余裕も無い (猶予2秒)。
     */
    @SuppressLint("MissingPermission")
    fun onIncomingCall(context: Context, inc: FragmentApi.Incoming) {
        ring(
            context,
            Incoming(
                room = "",
                number = inc.number,
                name = inc.name,
                // ⚠空にしておく。着信画面の見出しが既に「着信 · 出なければAIが預かります」と
                //   言っているので、ここに「着信」と書くと同じことを2回言うだけになる
                //   (実機で見て気づいた)。相手が何の用かはまだ誰も知らない
                reason = "",
                kind = Kind.CALL,
                // 鳴り始めは「番号検索中…」。検索が終わるとロングポーリングで同じ着信が
                // もう一度来て、ring() が中身だけ差し替える
                lookup = inc.lookup,
                announcing = inc.announcing,
            )
        )
    }

    /** 鳴っている呼び出しの同一性。⚠CALL はルームが無いので番号で見る */
    private fun Incoming.key(): String = if (kind == Kind.HANDOFF) "h:$room" else "c:$number"

    @SuppressLint("MissingPermission")
    private fun ring(context: Context, fresh: Incoming) {
        val prev = _incoming.value
        if (prev?.key() == fresh.key()) {
            // ⚠鳴っている間も相手はAIと話し続けている。同じ呼び出しでも中身は更新する
            //   (「迷っている間に判断材料が増える」のがこの画面の要点)。
            //   Telecomへの再登録はしない — 二重に鳴ってしまう
            _incoming.value = fresh
            if (prev.announcing && !fresh.announcing && uiShown) {
                // 録音告知が終わった。通知を着信音つき・「出る」つきに出し直す
                CallNotifications.showIncoming(context, fresh, screenVisible)
            }
            return
        }
        _incoming.value = fresh

        val tm = context.getSystemService(TelecomManager::class.java)
        if (tm == null) {
            Log.e(TAG, "TelecomManager unavailable — 通知だけの縮退動作へ")
            showIncomingUi(context)
            return
        }
        try {
            val extras = Bundle().apply {
                putParcelable(
                    TelecomManager.EXTRA_INCOMING_CALL_ADDRESS,
                    Uri.fromParts(PhoneAccount.SCHEME_TEL, fresh.number.ifEmpty { "unknown" }, null)
                )
                putString(EXTRA_ROOM, fresh.room)
            }
            tm.addNewIncomingCall(context.fragmentPhoneAccountHandle(), extras)
        } catch (e: SecurityException) {
            // PhoneAccountが無効化されている等。呼び出しを落とすより縮退させる方がまし
            Log.e(TAG, "addNewIncomingCall rejected — 縮退動作へ", e)
            showIncomingUi(context)
        }
    }

    /** 通知を出したか。告知中に出した通知を、告知の後で鳴る方へ出し直すのに要る */
    @Volatile
    private var uiShown = false

    /**
     * 着信画面 (IncomingCallActivity) が前に出ているか。告知の後に出し直す通知の種類を決める —
     * 出ていればバナー無しで鳴らす (CallNotifications.showIncoming)。
     * ⚠見え方が変わるたびに出し直しはしない。出し直すと着信音が頭から鳴り直す
     */
    @Volatile
    var screenVisible = false

    /**
     * Connection から呼ばれる。着信の通知を出す — **鳴らすのも画面を出すのも通知の仕事** (2026-09-25)。
     *
     * ⚠普通の電話アプリと同じ出し方にした (ユーザー「電話アプリなら、画面開いてるときは上の方に
     *   バナーが出てくるでしょ」):
     *   - ロック中・画面オフ … full-screen intent で着信画面 (IncomingCallActivity) が全画面で出る
     *   - 端末を使っている最中 … OS が上部のバナーに出し分ける。全画面にはしない
     *   以前は startActivity で画面を直接出していたが、使用中でも全画面になるうえ、
     *   アプリが裏にいると OS に止められていた (BAL_BLOCK、Fold で実測)。
     * ⚠着信音も通知 (CHANNEL_RING) から鳴らす。自前の MediaPlayer はおやすみモードで OS に
     *   消されていた (Pixel 9 で実測、`muted source:opPlayAudio`)。通知なら マナー/バイブ/おやすみ が
     *   普通の電話と同じ扱いになる (CATEGORY_CALL の通知は「通話」の許可設定で判定される)
     */
    fun showIncomingUi(context: Context) {
        uiShown = true
        CallNotifications.showIncoming(context, _incoming.value, screenVisible)
    }

    /**
     * 本人が「出る」を押した。
     *
     * ⚠**画面遷移は同期的に、通信は非同期に**、の順序が重要 (2026-08-01の実機で判明)。
     *   最初は「acceptを投げ、call idを解決してから startActivity」を全部コルーチンでやっていたが、
     *   呼び出し元の Activity が先に finish() するため、**バックグラウンドからのActivity起動**扱いで
     *   OSにブロックされ、画面が着信UIのまま固まった (acceptも届かず25秒後にexpire)。
     *   前面にいるうちに次の画面を開き、call idの解決は MainActivity 側でやる。
     */
    fun answer(context: Context) {
        val inc = _incoming.value ?: return
        if (inc.announcing) {
            // 録音告知中。ヘッドセット等の OS 側の応答ボタンから来ても取らない
            Log.i(TAG, "answer ignored: 録音告知中")
            return
        }
        stopRinging(context)
        connection?.setActiveSafely()
        answeredByUser = true

        // 投げっぱなし。⚠待っている相手 (AIの request_handoff / ダイヤルプランの
        //   /pickup_wait) がどちらもロングポーリングなので、早く投げるほどよい
        scope.launch {
            val api = FragmentApi(Prefs(context))
            runCatching {
                when (inc.kind) {
                    Kind.HANDOFF -> api.acceptHandoff(inc.room)
                    Kind.CALL -> api.pickup(inc.number)
                }
            }
        }
        // ⚠CALL はまだルームが無い (本人が出てから Asterisk がブリッジする)。
        //   room=null で開くと MainActivity が管制室のトップを出し、通話が成立した時点で
        //   常駐サービスが「通話中」カードを出す。そこから通話画面へ入れる
        context.startActivity(
            MainActivity.callIntent(context, inc.room.ifEmpty { null })
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        )
        _incoming.value = null
    }

    /**
     * 本人が出ている通話を切る (2026-09-24)。常駐通知の「通話終了」と、OS 側の終話
     * (腕時計・Bluetooth ヘッドセット・車載機) から来る。
     * ⚠以前は OS の終話が decline() に流れて**端末の中だけで切れ、サーバーの通話は続いていた**
     */
    fun hangup(context: Context) {
        val id = ServerState.activeCallId.value
        Log.i(TAG, "hangup (call=$id)")
        if (id != null) {
            scope.launch { runCatching { FragmentApi(Prefs(context)).hangupCall(id) } }
        }
        endConnection(DisconnectCause.LOCAL)
        answeredByUser = false
        jp.sleeptree.fragment.audio.CallAudio.endAll()
    }

    /**
     * サーバー側で通話が終わった (相手が切った・管制室で切った)。OS に通話終了を伝える。
     * ⚠これが無いと、応答後の Connection が ACTIVE のまま残り、OS は通話中だと思い続ける
     */
    fun onCallEnded() {
        endConnection(DisconnectCause.REMOTE)
        answeredByUser = false
    }

    private fun endConnection(cause: Int) {
        val c = connection ?: return
        connection = null
        try {
            c.setDisconnected(DisconnectCause(cause))
            c.destroy()
        } catch (e: Exception) {
            Log.w(TAG, "endConnection failed", e)
        }
    }

    /**
     * 「AIに任せる」(2026-09-25)。着信なら残りの呼び出しを待たずに AI に渡す
     * (「自分で出る」モードでもこの1本だけ AI が出る)。取り次ぎなら「AIに続けさせる」=
     * AI はすぐ「つかまりませんでした」と伝えて用件を預かる。
     * OS 側の拒否ボタン (ヘッドセット等) もここに来る — 相手を切るより穏当なので
     */
    fun toAi(context: Context) {
        val inc = _incoming.value ?: return
        scope.launch {
            val api = FragmentApi(Prefs(context))
            runCatching {
                when (inc.kind) {
                    Kind.HANDOFF -> api.declineHandoff(inc.room)
                    Kind.CALL -> api.decide(inc.number, "ai")
                }
            }
        }
        clear(context)
    }

    /** 「切る」(2026-09-25)。相手ごと切る。AI にも回さない。取り次ぎには無い (AI が既に話している) */
    fun reject(context: Context) {
        val inc = _incoming.value ?: return
        if (inc.kind != Kind.CALL) return toAi(context)
        scope.launch { runCatching { FragmentApi(Prefs(context)).decide(inc.number, "reject") } }
        clear(context)
    }

    /** 呼び出しが消えた (期限切れ・他の受け手が取った・AIに渡った)。端末の中だけ片付ける */
    fun expire(context: Context) {
        if (_incoming.value == null) return
        Log.i(TAG, "ringing expired")
        clear(context)
    }

    private fun clear(context: Context) {
        stopRinging(context)
        connection?.rejectSafely()
        connection = null
        _incoming.value = null
    }

    private fun stopRinging(context: Context) {
        uiShown = false
        CallNotifications.cancelIncoming(context)
    }

    /**
     * 見た目の確認用に、鳴らさず・Telecomも通さずに着信状態だけ作る。
     *
     * ⚠デバッグビルド専用。`adb shell am start -n jp.sleeptree.fragment/.ui.IncomingCallActivity --ez demo true`
     *   でエミュレータに着信画面を出せる。サーバーを立てずに配色と余白を見るためのもの。
     */
    fun seedForPreview() {
        _incoming.value = Incoming(
            room = "call_09012345678_demo",
            number = "090-1234-5678",
            name = "田中さん",
            reason = "急ぎの用件で本人と直接話したいとのこと",
            fragments = listOf(
                FragmentApi.Fragment("alert", "前回の見積もり未返答", "7/28に同件で入電し、折り返し希望を預かったまま返せていない"),
                FragmentApi.Fragment("info", "7/28にも同件で入電", "そのときも「今日中に決めたい」と急いでいた"),
            ),
            recent = listOf(
                FragmentApi.Segment("caller", "先日の見積もりの件で、どうしても直接お話ししたくて"),
                FragmentApi.Segment("ai", "わかりました。確認してみますね"),
                FragmentApi.Segment("caller", "待ちます。今日中に決めたいもので"),
            ),
        )
    }

    const val EXTRA_ROOM = "jp.sleeptree.fragment.ROOM"
    private const val TAG = "CallCoordinator"
}
