package jp.sleeptree.fragment.call

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log
import jp.sleeptree.fragment.DevicePause
import jp.sleeptree.fragment.Prefs
import jp.sleeptree.fragment.ServerState
import jp.sleeptree.fragment.WatchService
import jp.sleeptree.fragment.api.FragmentApi

/**
 * 通知のボタン (AIに任せる/切る) と、常駐通知の操作 (応答モード・一時停止)。
 * ⚠「出る」はここを通らない — 通知から BroadcastReceiver 経由で画面を開くのは Android 12+ で
 *   禁止 (notification trampoline)。出るは着信画面を直接開く (CallNotifications.showIncoming)
 */
class CallActionReceiver : BroadcastReceiver() {

    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            ACTION_TO_AI -> CallCoordinator.toAi(context)
            ACTION_REJECT -> CallCoordinator.reject(context)
            // ⚠2026-09-24 まではここが端末のタイマーを動かすだけで、**サーバーの応答モードを
            //   変えていなかった**。アプリ上部の切替 (これはサーバーに送っていた) を撤去した日に、
            //   常駐通知からモードを変える手段が実質無くなっていた
            ACTION_STANDBY_EXTEND -> {
                Prefs(context).startStandby()
                setMode(context, "standby")
            }
            ACTION_STANDBY_STOP -> {
                Prefs(context).clearStandby()
                setMode(context, "away")
            }
            ACTION_HANGUP -> CallCoordinator.hangup(context)
            ACTION_PAUSE -> DevicePause.set(context, true)
            ACTION_RESUME -> DevicePause.set(context, false)
        }
    }

    private fun setMode(context: Context, mode: String) {
        val pending = goAsync()
        Thread {
            try {
                val ok = FragmentApi(Prefs(context)).setAnswerMode(mode)
                if (ok) ServerState.setAnswerMode(mode)
                Log.i(TAG, "answer_mode=$mode: ${if (ok) "ok" else "失敗"}")
                WatchService.refresh(context)
            } finally {
                pending.finish()
            }
        }.start()
    }

    companion object {
        private const val TAG = "CallActionReceiver"
        const val ACTION_TO_AI = "jp.sleeptree.fragment.TO_AI"
        const val ACTION_REJECT = "jp.sleeptree.fragment.REJECT"
        const val ACTION_STANDBY_EXTEND = "jp.sleeptree.fragment.STANDBY_EXTEND"
        const val ACTION_STANDBY_STOP = "jp.sleeptree.fragment.STANDBY_STOP"
        const val ACTION_HANGUP = "jp.sleeptree.fragment.HANGUP"
        const val ACTION_PAUSE = "jp.sleeptree.fragment.PAUSE"
        const val ACTION_RESUME = "jp.sleeptree.fragment.RESUME"
    }
}
