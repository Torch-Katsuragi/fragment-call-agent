package jp.sleeptree.fragment.call

import android.telecom.Connection
import android.telecom.ConnectionRequest
import android.telecom.ConnectionService
import android.telecom.DisconnectCause
import android.telecom.PhoneAccountHandle
import android.util.Log

/**
 * 自己管理型 (self-managed) の ConnectionService。
 *
 * 標準の電話アプリを置き換えるものではない。OSに「うちも通話を持っている」と知らせて、
 * 音声ルーティング・他アプリの通話との調停・Bluetoothへの見え方を任せるための窓口。
 * **着信UIとベル鳴らしはこちら側の責任** (CAPABILITY_SELF_MANAGED の契約)。
 */
class FragmentConnectionService : ConnectionService() {

    override fun onCreateIncomingConnection(
        connectionManagerPhoneAccount: PhoneAccountHandle?,
        request: ConnectionRequest?,
    ): Connection {
        val room = request?.extras?.getString(CallCoordinator.EXTRA_ROOM).orEmpty()
        Log.i(TAG, "onCreateIncomingConnection room=$room")
        return FragmentConnection(applicationContext).also {
            CallCoordinator.connection = it
            it.setRinging()
        }
    }

    override fun onCreateIncomingConnectionFailed(
        connectionManagerPhoneAccount: PhoneAccountHandle?,
        request: ConnectionRequest?,
    ) {
        // ⚠ここに来るのは他アプリが通話中で割り込めない等。呼び出しを黙って落とさず記録する
        Log.w(TAG, "onCreateIncomingConnectionFailed")
        CallCoordinator.connection = null
    }

    override fun onCreateOutgoingConnection(
        connectionManagerPhoneAccount: PhoneAccountHandle?,
        request: ConnectionRequest?,
    ): Connection {
        // 発信はアプリの担当外 (管制室から掛ける)。要求が来たら明示的に断る
        return Connection.createFailedConnection(
            DisconnectCause(DisconnectCause.ERROR, "outgoing is not supported")
        )
    }

    companion object {
        private const val TAG = "FragmentConnSvc"
    }
}
