package jp.sleeptree.fragment

import android.content.Context
import android.content.SharedPreferences

/**
 * 端末側の設定。
 *
 * ⚠スタンバイは**明示的な操作でしか変わらない**。アプリの起動状態・WebViewの表示状態からは
 *   絶対に推測しない (2026-07-31 ユーザーの指示)。「開いているか」は「対応する気があるか」を
 *   答えないので、タブを開きっぱなしで放置されると誤って在席と判定してしまう。
 *   ただし**切り忘れ**も同じ害 (AIが「呼んでみます」と言って誰も出ない) なので、
 *   時限を持たせて既定8時間で自動的に不在へ落とす。延長・解除は常駐通知から行う。
 */
class Prefs(context: Context) {

    private val sp: SharedPreferences =
        context.getSharedPreferences("fragment", Context.MODE_PRIVATE)

    /** 管制室のURL。例: https://fragment.example.com */
    var baseUrl: String
        get() = sp.getString(KEY_BASE_URL, "") ?: ""
        set(v) = sp.edit().putString(KEY_BASE_URL, v.trimEnd('/')).apply()

    /**
     * 端末トークン。管制室の設定画面が出すQRから取り込む。
     * ⚠WebViewの中ではGoogleログインができない (`disallowed_useragent` で拒否される) ため、
     *   アプリはOAuthではなくこのトークンで認証する。LiveKitのoperatorトークン取得にも同じものを使う。
     */
    var deviceToken: String
        get() = sp.getString(KEY_TOKEN, "") ?: ""
        set(v) = sp.edit().putString(KEY_TOKEN, v.trim()).apply()

    val isConfigured: Boolean
        get() = baseUrl.isNotEmpty() && deviceToken.isNotEmpty()

    /** Firebase (FCM) の接続情報 JSON。管制室の /api/device/config から。空 = 未取得かサーバーに無い */
    var firebaseOptions: String
        get() = sp.getString(KEY_FIREBASE, "") ?: ""
        set(v) = sp.edit().putString(KEY_FIREBASE, v).apply()

    /** 管制室に登録済みの FCM トークン (同じものを毎回送らないため) */
    var pushRegisteredToken: String
        get() = sp.getString(KEY_PUSH_TOKEN, "") ?: ""
        set(v) = sp.edit().putString(KEY_PUSH_TOKEN, v).apply()

    /**
     * AI が応対している間、ロック画面の上に何を出すか (2026-09-19 ユーザーの指示で 3 択)。
     * none = 何も出さない / visualizer = 名前 + 円状の波形 / chat = 会話ストリームだけ
     */
    /**
     * 終話通知を出し済みの通話 id (2026-09-25)。同じ通話を二度知らせない。
     * ⚠1件だけ覚えていた頃は、続けて2本終わると先の1本が通知されなかった。直近20件を覚える
     */
    /** endedNotified を一度でも書いたか。⚠更新直後の1回目に、直近の通話をまとめて通知し直さないため */
    val endedNotifiedInitialized: Boolean
        get() = sp.contains(KEY_ENDED_NOTIFIED)

    var endedNotified: List<String>
        get() = (sp.getString(KEY_ENDED_NOTIFIED, "") ?: "").split(",").filter { it.isNotEmpty() }
        set(v) = sp.edit().putString(KEY_ENDED_NOTIFIED, v.takeLast(20).joinToString(",")).apply()

    var liveDisplay: String
        get() = sp.getString(KEY_LIVE_DISPLAY, LIVE_NONE) ?: LIVE_NONE
        set(v) = sp.edit().putString(KEY_LIVE_DISPLAY, v).apply()

    /**
     * この端末の一時停止 (2026-09-24)。true の間は着信・取り次ぎで鳴らさない。
     * サーバーにも伝える (DevicePause) — 全端末が一時停止ならスタンバイの待ちを飛ばすため
     */
    var paused: Boolean
        get() = sp.getBoolean(KEY_PAUSED, false)
        set(v) = sp.edit().putBoolean(KEY_PAUSED, v).apply()

    /**
     * 通話画面を開いていないときも AI 応対中の音を流すか (2026-09-18 ユーザー「その画面を開いてたら
     * 確定で流す、それ以外は設定次第」)。⚠既定は流さない。端末ごとの値 (PC の管制室とは別)
     */
    var monitorOutside: Boolean
        get() = sp.getBoolean(KEY_MONITOR_OUTSIDE, false)
        set(v) = sp.edit().putBoolean(KEY_MONITOR_OUTSIDE, v).apply()

    /** スタンバイの期限 (epoch millis)。0 = スタンバイではない */
    var standbyUntil: Long
        get() = sp.getLong(KEY_STANDBY_UNTIL, 0L)
        set(v) = sp.edit().putLong(KEY_STANDBY_UNTIL, v).apply()

    val isStandby: Boolean
        get() = standbyUntil > System.currentTimeMillis()

    /** 残り時間 (ミリ秒)。スタンバイでなければ0 */
    val standbyRemainingMs: Long
        get() = (standbyUntil - System.currentTimeMillis()).coerceAtLeast(0L)

    fun startStandby(hours: Int = STANDBY_DEFAULT_HOURS) {
        standbyUntil = System.currentTimeMillis() + hours * 3600_000L
    }

    fun clearStandby() {
        standbyUntil = 0L
    }

    companion object {
        private const val KEY_BASE_URL = "base_url"
        private const val KEY_TOKEN = "device_token"
        private const val KEY_STANDBY_UNTIL = "standby_until"
        private const val KEY_FIREBASE = "firebase_options"
        private const val KEY_PUSH_TOKEN = "push_registered_token"
        private const val KEY_LIVE_DISPLAY = "live_display"
        private const val KEY_ENDED_NOTIFIED = "ended_notified"
        private const val KEY_PAUSED = "paused"
        private const val KEY_MONITOR_OUTSIDE = "monitor_outside"

        const val LIVE_NONE = "none"
        const val LIVE_VISUALIZER = "visualizer"
        const val LIVE_CHAT = "chat"

        const val STANDBY_DEFAULT_HOURS = 8
    }
}
