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
    private var ongoingAnswered = false

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
        // スタンバイの期限切れ。⚠ここで落とすのは「切り忘れ」対策であって、
        //   アプリの起動状態から推測しているわけではない (Prefs の注記参照)
        if (prefs.standbyUntil != 0L && !prefs.isStandby) {
            prefs.clearStandby()
            api.setAnswerMode("away")
            updateWatchNotification()
        }

        if (!prefs.isConfigured) return false
        val state = api.deviceState(waitSec = WAIT_SEC, since = lastV) ?: return false
        lastV = state.v
        // ⚠応答モードの正はサーバー。管制室から切り替えられることがあるので、
        //   端末のスタンバイタイマーだけを見て表示を作らない (ズレる)
        ServerState.setAnswerMode(state.answerMode)
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

        // --- 「通話中」の通知 ---
        // ⚠AI が応対しているだけの間は出さない (2026-09-25 ユーザー「通話中カードはやめて」)。
        //   出すのは、本人が出た通話 (「通話終了」ボタンの置き場) と、
        //   「AI応対中の表示」を設定でオンにしているとき (その画面を起こす full-screen intent の器) だけ
        val active = state.activeCall
        ServerState.setActiveCall(active)
        val answered = CallCoordinator.answeredByUser
        if (active != null && (active.room != ongoingRoom || answered != ongoingAnswered)) {
            ongoingRoom = active.room
            ongoingAnswered = answered
            // 本人が出た通話には「AI応対中の表示」を被せない (管制室が既に前面にいる)
            val live = if (answered) Prefs.LIVE_NONE else prefs.liveDisplay
            if (answered || live != Prefs.LIVE_NONE) {
                CallNotifications.showOngoing(this, active.id, active.name ?: active.number, live, answered)
            } else {
                CallNotifications.cancelOngoing(this)
            }
        } else if (active == null && ongoingRoom != null) {
            ongoingRoom = null
            ongoingAnswered = false
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
     * (2026-07-31の決定: 時限つき + 常駐通知から延長)。
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

        // ⚠表示するモードはサーバーの値。取れていない間だけ端末のタイマーで代用する
        when (ServerState.answerMode.value ?: if (prefs.isStandby) "standby" else "away") {
            "standby" -> {
                val mins = prefs.standbyRemainingMs / 60000
                b.setContentTitle("スタンバイ中")
                    .setContentText(
                        if (mins > 0) "あと ${mins / 60}時間${mins % 60}分 · 数秒鳴らします"
                        else "数秒鳴らして、出なければAIが応対します"
                    )
                    .addAction(action(11, "延長", CallActionReceiver.ACTION_STANDBY_EXTEND))
                    .addAction(action(12, "解除", CallActionReceiver.ACTION_STANDBY_STOP))
                    .addAction(pause)
            }
            "manual" -> {
                b.setContentTitle("自分で出る")
                    .setContentText("AIは応答しません")
                    .addAction(action(12, "スタンバイに戻す", CallActionReceiver.ACTION_STANDBY_EXTEND))
                    .addAction(pause)
            }
            else -> {
                b.setContentTitle("不在")
                    .setContentText("AIが用件を預かります")
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
