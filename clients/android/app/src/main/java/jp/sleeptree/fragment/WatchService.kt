package jp.sleeptree.fragment

import android.app.Notification
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.util.Log
import jp.sleeptree.fragment.api.FragmentApi
import jp.sleeptree.fragment.call.CallActionReceiver
import jp.sleeptree.fragment.call.CallCoordinator
import jp.sleeptree.fragment.call.CallNotifications
import jp.sleeptree.fragment.push.PushSetup
import jp.sleeptree.fragment.ui.MainActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch

/**
 * 管制室を見張る常駐サービス。取り次ぎ要求と着信が来たら鳴らす。
 *
 * ⚠**ロングポーリング** (2026-08-01に1.5秒間隔から変更)。スタンバイの猶予は2秒しかなく、
 *   1.5秒間隔だと**猶予をまるごと取りこぼす**ことがあった。サーバーは状態が変わるまで返さない。
 *   WebSocketやFCMに置き換えるのはこのクラスの中だけで済む — 呼び出しの発火は
 *   CallCoordinator に閉じているので、運び方を変えても外に影響しない。
 *
 * ⚠**据え置き端末なら常時給電でこれで足りるが、ポケットの携帯ではDozeで止まる**。
 *   メイン端末で常用に移す判断をしたらFCMを足す (設計mdの「端末の計画」)。
 */
class WatchService : Service() {

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private lateinit var prefs: Prefs
    private lateinit var api: FragmentApi

    /** いま「通話中」カードを出している通話 */
    private var ongoingRoom: String? = null

    /** いまの「通話中」カードが「自分が出ている」版か。出た瞬間に描き直す (通話終了ボタンを付ける) */
    /** 通知に出している中身 (ルーム・見出し・自分が会話中か)。変わったら出し直す */
    private var ongoingKey: String? = null

    /** ロングポーリングの継続に使う指紋。⚠通信に失敗しても捨てない (同じ状態を取り直すだけ) */
    private var lastV: String = ""

    override fun onCreate() {
        super.onCreate()
        prefs = Prefs(this)
        api = FragmentApi(prefs)
        startForegroundCompat()
        scope.launch { loop() }
        scope.launch { pushLoop() }
    }

    /**
     * FCM の準備 (2026-09-18)。接続情報の取得 → 初期化 → トークン登録。
     * ⚠ロングポーリングとは別のコルーチンで回す — ここが失敗しても見張りを止めない。
     *   サーバーに FCM が無ければ 10 分おきに聞き直す (後から有効にしたとき再ペアリング無しで拾う)
     */
    private suspend fun pushLoop() {
        while (scope.isActive) {
            if (!prefs.isConfigured) { delay(5_000L); continue }
            val r = try {
                PushSetup.ensureRegistered(this, prefs, api)
            } catch (e: Exception) {
                Log.w(TAG, "push setup failed", e)
                PushSetup.Result.RETRY
            }
            when (r) {
                PushSetup.Result.DONE -> {
                    // 登録し直したトークンは paused=false で入るので、端末の状態を伝え直す
                    jp.sleeptree.fragment.DevicePause.sync(prefs)
                    return
                }
                PushSetup.Result.NO_SERVER_PUSH -> delay(10 * 60_000L)
                PushSetup.Result.RETRY -> delay(60_000L)
            }
        }
    }

    private suspend fun loop() {
        while (scope.isActive) {
            val ok = try {
                tick()
            } catch (e: Exception) {
                Log.w(TAG, "tick failed", e)
                false
            }
            // ⚠成功時は待たない — ロングポーリングが既にサーバー側で待っている。
            //   ここで足すと猶予2秒に対して致命的に遅れる。失敗時だけ間を置く
            if (!ok) delay(RETRY_MS)
        }
    }

    /** @return サーバーから状態を取れたか (取れなかったら少し待って張り直す) */
    private fun tick(): Boolean {
        if (!prefs.isConfigured) return false
        val state = api.deviceState(waitSec = WAIT_SEC, since = lastV) ?: return false
        lastV = state.v
        // ⚠応答モードの正はサーバー。管制室から切り替えられることがあるので、
        //   端末のスタンバイタイマーだけを見て表示を作らない (ズレる)
        ServerState.setAnswerMode(state.answerMode)
        ServerState.setModeInfo(state.modeInfo)
        updateWatchNotification()

        // --- 呼び出し ---
        // ⚠取り次ぎを優先する。同時に立つことは構造上ほぼ無いが、立ったら
        //   「AIが応対中の通話に呼ばれている」方が緊急度が高い
        val h = state.handoff
        val inc = state.incoming
        when {
            // 一時停止中は鳴らさない (2026-09-24)。鳴っていたものは止める
            prefs.paused -> if (CallCoordinator.incoming.value != null) CallCoordinator.expire(this)
            h != null -> CallCoordinator.onHandoffRequested(this, h)
            // 不在(away)はサーバーが ring_ms=0 を返すのでここに来ない。
            // 「鳴らすかどうか」をサーバー側の応答モードで決めているのが要点 —
            // 端末が自分の状態から推測すると、切り替えの瞬間に食い違う
            inc != null -> CallCoordinator.onIncomingCall(this, inc)
            CallCoordinator.incoming.value != null ->
                // 期限切れ・他の受け手が取った・AIに渡った
                CallCoordinator.expire(this)
        }

        // --- 「通話中」の通知 = 待機中の入口 (2026-09-26) ---
        // 通話があれば、どの端末にも音の出ない通知を1枚出す (ロック画面には出さない)。見出しは担い手
        // 「AIが応対中」「〇〇が応対中」「保留中」で、押せば視聴、「話す」で会話に入る。
        // ⚠2026-09-25 に「AI 応対中の通話中カードはやめて」で一度消したが、全端末が待機・視聴・会話を
        //   行き来できるようにしたので入口として戻した (ユーザー了承済み、通知欄だけ)。
        // ⚠自分が会話中かはサーバーの参加者 (presence) で見る。入った直後はまだ載っていないので
        //   手元の接続状態も見る
        val active = state.activeCall
        ServerState.setActiveCall(active)
        if (active != null) {
            val audio = jp.sleeptree.fragment.audio.CallAudio.state.value
            val talking = active.presence.any { it.id == prefs.deviceId && it.role == "talk" } ||
                (audio.operator && audio.room == active.room)
            val key = "${active.room}|${active.handlerLabel}|$talking"
            if (key != ongoingKey) {
                // 「AI応対中の表示」(ロック画面の全画面) を起こすのは、通話が現れた最初の1回だけ。
                // 見出しが変わるたびに起こすと、ロック中の画面が何度も点く
                val first = active.room != ongoingRoom
                ongoingRoom = active.room
                ongoingKey = key
                val live = if (talking) Prefs.LIVE_NONE else prefs.liveDisplay
                CallNotifications.showOngoing(this, active, live, talking, fullScreen = first)
            }
        } else if (ongoingRoom != null) {
            ongoingRoom = null
            ongoingKey = null
            // 相手が切った・管制室で切った。OS に通話終了を伝える (応答後の Connection を残さない)
            CallCoordinator.onCallEnded()
            jp.sleeptree.fragment.audio.CallAudio.endAll()
            CallNotifications.cancelOngoing(this)
        }

        // --- 終話通知 (2026-09-25) ---
        // AI が受けた通話が終わり、worker の要約ができたら1回だけ出す。まだ出していない分を古い順に
        val notified = prefs.endedNotified
        val fresh = state.recentEnded.filter { it.id !in notified }.reversed()
        if (!prefs.endedNotifiedInitialized) {
            // 初回 (入れた直後・更新直後) は今ある分を覚えるだけ。既に知らせた通話を出し直さない
            prefs.endedNotified = state.recentEnded.map { it.id }.reversed()
        } else if (fresh.isNotEmpty()) {
            prefs.endedNotified = notified + fresh.map { it.id }
            // 一時停止中でも出す (止めるのは鳴らすことだけ)
            fresh.forEach { CallNotifications.showEnded(this, it) }
        }
        return true
    }

    private fun startForegroundCompat() {
        val n = buildWatchNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                FragmentApp.NOTIF_WATCH, n,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_PHONE_CALL
            )
        } else {
            startForeground(FragmentApp.NOTIF_WATCH, n)
        }
    }

    private fun updateWatchNotification() {
        val nm = getSystemService(android.app.NotificationManager::class.java) ?: return
        nm.notify(FragmentApp.NOTIF_WATCH, buildWatchNotification())
    }

    /**
     * 常駐通知。**スタンバイの残り時間をここに出し、延長と解除もここからできる**
     * (2026-07-31の決定: 時限つき + 常駐通知から延長)。2026-09-29 から時限と時間割はサーバーが持ち、
     * ここは次の切り替わり (ServerState.modeInfoText) を出すだけ。
     */
    private fun buildWatchNotification(): Notification {
        val open = PendingIntent.getActivity(
            context(), 10,
            Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val b = Notification.Builder(this, FragmentApp.CHANNEL_WATCH)
            .setSmallIcon(R.drawable.ic_notification)
            .setOngoing(true)
            .setContentIntent(open)
            .setVisibility(Notification.VISIBILITY_PUBLIC)

        // 一時停止中 (2026-09-24)。応答モードより先に見せる — 「鳴らない」理由がこれなので
        if (prefs.paused) {
            return b.setContentTitle("一時停止中")
                .setContentText("この端末は鳴りません (AIの応対と他の端末はそのまま)")
                .addAction(action(13, "再開", CallActionReceiver.ACTION_RESUME))
                .build()
        }
        val pause = action(14, "一時停止", CallActionReceiver.ACTION_PAUSE)

        // ⚠表示するモードはサーバーの値。時限と時間割の次の切り替わりもサーバーが返す (2026-09-29)
        val sched = ServerState.modeInfoText(ServerState.modeInfo.value)
        when (ServerState.answerMode.value ?: "away") {
            "standby" -> {
                b.setContentTitle("スタンバイ中")
                    .setContentText(sched ?: "数秒鳴らして、出なければAIが応対します")
                    .apply {
                        // 延長 = 8 時間を数え直す。時間割のときは時間割が区切るので出さない
                        if (ServerState.modeInfo.value?.scheduleEnabled != true) {
                            addAction(action(11, "延長", CallActionReceiver.ACTION_STANDBY_EXTEND))
                        }
                    }
                    .addAction(action(12, "解除", CallActionReceiver.ACTION_STANDBY_STOP))
                    .addAction(pause)
            }
            "manual" -> {
                b.setContentTitle("自分で出る")
                    .setContentText(sched ?: "AIは応答しません")
                    .addAction(action(12, "スタンバイに戻す", CallActionReceiver.ACTION_STANDBY_EXTEND))
                    .addAction(pause)
            }
            else -> {
                b.setContentTitle("不在")
                    .setContentText(sched ?: "AIが用件を預かります")
                    .addAction(action(11, "スタンバイ", CallActionReceiver.ACTION_STANDBY_EXTEND))
                    .addAction(pause)
            }
        }
        return b.build()
    }

    private fun action(code: Int, label: String, act: String): Notification.Action =
        Notification.Action.Builder(
            null, label,
            PendingIntent.getBroadcast(
                this, code,
                Intent(this, CallActionReceiver::class.java).setAction(act),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
        ).build()

    private fun context(): Context = this

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        // 常駐通知の描き直し (一時停止・モード変更の直後に。次のロングポーリングを待たない)
        if (intent?.action == ACTION_REFRESH) updateWatchNotification()
        return START_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    companion object {
        private const val TAG = "WatchService"

        /** サーバーにぶら下がる秒数。⚠サーバー側の上限 (25秒) を超えないこと */
        private const val WAIT_SEC = 20

        /** 張り直しの間隔 (通信失敗時のみ) */
        private const val RETRY_MS = 2000L

        private const val ACTION_REFRESH = "jp.sleeptree.fragment.WATCH_REFRESH"

        fun start(context: Context) {
            context.startForegroundService(Intent(context, WatchService::class.java))
        }

        /** 常駐通知を今すぐ描き直す。⚠前面サービスが動いている前提 (動いていなければ何もしない) */
        fun refresh(context: Context) {
            try {
                context.startService(Intent(context, WatchService::class.java).setAction(ACTION_REFRESH))
            } catch (e: Exception) {
                Log.w(TAG, "refresh failed: ${e.message}")
            }
        }
    }
}
