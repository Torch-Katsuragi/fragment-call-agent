package jp.sleeptree.fragment

import android.content.Context
import android.util.Log
import jp.sleeptree.fragment.api.FragmentApi

/**
 * この端末の一時停止 (2026-09-24 ユーザー「アプリ側で一時停止できたらいい」)。
 *
 * ⚠応答モード (away/standby/manual/record) は回線 (050) 全体の設定で、PC と全端末で 1 つを共有する。
 *   一時停止はそれとは別に「この端末だけ鳴らない」。AI の応対と他の端末には影響しない。
 * ⚠端末側でも鳴らさない (WatchService / PushService) し、サーバーにも伝える。サーバーは
 *   一時停止中の端末に「起きろ」を送らず、全端末が一時停止ならスタンバイの待ちを飛ばす (hookd)
 */
object DevicePause {
    private const val TAG = "DevicePause"

    fun set(context: Context, paused: Boolean) {
        val prefs = Prefs(context)
        prefs.paused = paused
        WatchService.refresh(context)
        sync(prefs)
    }

    /** サーバーへ伝える。FCM の登録がまだ無い端末は伝えられない (端末側で鳴らさないのは効く) */
    fun sync(prefs: Prefs) {
        val token = prefs.pushRegisteredToken
        if (token.isEmpty()) return
        val paused = prefs.paused
        Thread {
            val ok = runCatching { FragmentApi(prefs).setPaused(token, paused) }.getOrDefault(false)
            Log.i(TAG, "paused=$paused をサーバーへ: ${if (ok) "ok" else "失敗"}")
        }.start()
    }
}
