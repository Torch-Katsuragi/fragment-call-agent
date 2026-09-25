package jp.sleeptree.fragment.push

import android.util.Log
import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import jp.sleeptree.fragment.Prefs
import jp.sleeptree.fragment.ServerState
import jp.sleeptree.fragment.WatchService
import jp.sleeptree.fragment.api.FragmentApi
import jp.sleeptree.fragment.call.CallCoordinator

/**
 * FCM の受け口 (2026-09-18)。hookd が着信・取り次ぎの瞬間に「起きろ」を送ってくる。
 *
 * ⚠中身は type (ring / handoff) だけ。**鳴らすかどうかも誰からかも、ここでは決めない**。
 *   起きたら /api/device/state を1回取りに行き、WatchService の tick と同じ判断に流す。
 *   ロングポーリング (WatchService) が生きていれば同じ状態を二重に見るが、
 *   CallCoordinator が同一性 (番号 / ルーム) で弾くので二重には鳴らない。
 *
 * ⚠onMessageReceived はワーカースレッドで呼ばれ、高優先度メッセージなら Doze 中でも
 *   約10秒のネットワーク猶予がある。その中で取りに行く (別スレッドに逃がさない)。
 */
class PushService : FirebaseMessagingService() {

    override fun onNewToken(token: String) {
        val prefs = Prefs(this)
        if (!prefs.isConfigured) return
        // 再インストール等でトークンが変わった。古いものはサーバー側が UNREGISTERED で消す
        Thread { PushSetup.register(prefs, FragmentApi(prefs), token) }.start()
    }

    override fun onMessageReceived(msg: RemoteMessage) {
        val type = msg.data["type"] ?: "?"
        Log.i(TAG, "push received: $type")
        val prefs = Prefs(this)
        if (!prefs.isConfigured) return
        if (prefs.paused) {
            // 一時停止中 (2026-09-24)。サーバーも送らないはずだが、伝わる前の一瞬のため
            Log.i(TAG, "push $type: 一時停止中なので鳴らさない")
            return
        }
        // Doze で止まっていた見張りを起こす (以後はロングポーリングに戻る)
        try {
            WatchService.start(this)
        } catch (e: Exception) {
            Log.w(TAG, "WatchService.start failed", e)
        }
        val st = FragmentApi(prefs).deviceState() ?: return
        ServerState.setAnswerMode(st.answerMode)
        val h = st.handoff
        val inc = st.incoming
        when {
            h != null -> CallCoordinator.onHandoffRequested(this, h)
            inc != null -> CallCoordinator.onIncomingCall(this, inc)
            else -> Log.i(TAG, "push $type: 鳴らすものは無かった (既に終わったか、不在モード)")
        }
    }

    companion object {
        private const val TAG = "PushService"
    }
}
