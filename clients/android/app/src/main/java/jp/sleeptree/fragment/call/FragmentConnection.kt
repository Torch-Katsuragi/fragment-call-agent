package jp.sleeptree.fragment.call

import android.content.Context
import android.telecom.Connection
import android.telecom.DisconnectCause
import android.util.Log

/**
 * 1本の取り次ぎ呼び出し。
 *
 * ⚠OSのボタン (Bluetoothヘッドセットの応答ボタン・車載機など) からも onAnswer が来る。
 *   だから応答処理は必ずここを通し、画面のボタンも同じ経路を叩く (CallCoordinator.answer)。
 */
class FragmentConnection(private val context: Context) : Connection() {

    init {
        connectionProperties = PROPERTY_SELF_MANAGED
        audioModeIsVoip = true
        connectionCapabilities = CAPABILITY_HOLD or CAPABILITY_SUPPORT_HOLD
    }

    /**
     * 自己管理型でだけ呼ばれる。**ここが着信UIを出す唯一の合図**。
     * ⚠`setRinging()` を呼んだだけでは何も起きない — OSは自己管理型の通話UIを描かない。
     */
    override fun onShowIncomingCallUi() {
        Log.i(TAG, "onShowIncomingCallUi")
        CallCoordinator.showIncomingUi(context)
    }

    override fun onAnswer() {
        Log.i(TAG, "onAnswer (from OS)")
        CallCoordinator.answer(context)
    }

    override fun onReject() {
        Log.i(TAG, "onReject (from OS)")
        // OS の拒否 (ヘッドセットのボタン等) は「AIに任せる」扱い。相手ごと切るより穏当
        CallCoordinator.toAi(context)
    }

    override fun onDisconnect() {
        Log.i(TAG, "onDisconnect (state=$state)")
        // 出たあとの終話 (腕時計・Bluetooth・車載機) はサーバーの通話も切る。鳴っている間なら断るだけ
        if (state == STATE_ACTIVE) CallCoordinator.hangup(context) else CallCoordinator.toAi(context)
    }

    /** OSが「他の通話に譲れ」と言ってきた。素直に降りる */
    override fun onCallAudioStateChanged(state: android.telecom.CallAudioState?) {
        Log.d(TAG, "audio state: $state")
    }

    fun setActiveSafely() {
        try {
            setActive()
        } catch (e: Exception) {
            Log.w(TAG, "setActive failed", e)
        }
    }

    fun rejectSafely() {
        try {
            setDisconnected(DisconnectCause(DisconnectCause.LOCAL))
            destroy()
        } catch (e: Exception) {
            Log.w(TAG, "reject failed", e)
        }
    }

    companion object {
        private const val TAG = "FragmentConnection"
    }
}
