package jp.sleeptree.fragment

import android.app.Application
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.ComponentName
import android.net.Uri
import android.telecom.PhoneAccount
import android.telecom.PhoneAccountHandle
import android.telecom.TelecomManager
import android.util.Log
import jp.sleeptree.fragment.call.FragmentConnectionService

class FragmentApp : Application() {

    override fun onCreate() {
        super.onCreate()
        registerPhoneAccount()
        createChannels()
        // FCM で起こされたプロセスは PushService より先にここを通る。保存済みの接続情報で初期化しておく
        // (接続情報が無ければ何もしない。取りに行くのは WatchService)
        jp.sleeptree.fragment.push.PushSetup.initFirebase(this, Prefs(this))
    }

    /**
     * 自己管理型 (self-managed) の PhoneAccount を登録する。
     *
     * ⚠CAPABILITY_SELF_MANAGED を付けると「着信UIは自分で出す」契約になる。
     *   OSは通話UIを出してくれない代わりに、音声ルーティング・他の通話との調停・
     *   Bluetooth への通話の見え方を面倒見てくれる。標準の電話アプリを置き換えるわけではない。
     *
     * ⚠登録に MANAGE_OWN_CALLS 権限が要る (マニフェスト宣言のみ・実行時要求は不要)。
     */
    private fun registerPhoneAccount() {
        try {
            val tm = getSystemService(TelecomManager::class.java) ?: return
            val account = PhoneAccount.builder(phoneAccountHandle(), getString(R.string.app_name))
                .setCapabilities(PhoneAccount.CAPABILITY_SELF_MANAGED)
                // 自分の「番号」= 管制室が預かっている050。表示にしか使わない
                .setAddress(Uri.fromParts(PhoneAccount.SCHEME_SIP, "fragment", null))
                .setShortDescription(getString(R.string.account_description))
                .build()
            tm.registerPhoneAccount(account)
        } catch (e: Exception) {
            // ⚠ここで落とさない。PhoneAccountが登録できない端末でも、
            //   通知だけの縮退動作 (鳴らして管制室を開く) はできるようにしておく
            Log.e(TAG, "registerPhoneAccount failed", e)
        }
    }

    private fun createChannels() {
        val nm = getSystemService(NotificationManager::class.java) ?: return
        // 着信 (録音告知中): full-screen intent の受け皿。告知の間は鳴らさないので無音
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_INCOMING, "着信", NotificationManager.IMPORTANCE_HIGH).apply {
                setSound(null, null)
                enableVibration(false)
                description = "取り次ぎの呼び出し"
            }
        )
        // 着信 (告知の後): ここが着信音の音源 (2026-09-25)。端末の着信音をそのまま使う。
        // ⚠自前の MediaPlayer で鳴らしていた頃は、おやすみモードで OS に消されていた。
        //   通知の音ならマナー/バイブ/おやすみが普通の電話と同じ扱いになる。
        // ⚠チャンネルの音は作成後に変えられない。音を変えるならチャンネル id を変える
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_RING, "着信音", NotificationManager.IMPORTANCE_HIGH).apply {
                setSound(
                    android.provider.Settings.System.DEFAULT_RINGTONE_URI,
                    android.media.AudioAttributes.Builder()
                        .setUsage(android.media.AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                        .setContentType(android.media.AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build(),
                )
                enableVibration(true)
                // 1秒振動 → 2秒休み。日本の呼出音のリズム
                vibrationPattern = longArrayOf(0, 1000, 2000)
                description = "着信と取り次ぎの呼び出し"
            }
        )
        // 着信 (告知の後・着信画面が前に出ているとき): 音とバイブだけ、バナーは出さない。
        // ⚠IMPORTANCE_HIGH のままだと、全画面の着信画面の上にバナーが重なる (Pixel 9 で実測)。
        //   DEFAULT は鳴るが画面の上に出てこない
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_RING_ONSCREEN, "着信音 (着信画面の表示中)", NotificationManager.IMPORTANCE_DEFAULT).apply {
                setSound(
                    android.provider.Settings.System.DEFAULT_RINGTONE_URI,
                    android.media.AudioAttributes.Builder()
                        .setUsage(android.media.AudioAttributes.USAGE_NOTIFICATION_RINGTONE)
                        .setContentType(android.media.AudioAttributes.CONTENT_TYPE_SONIFICATION)
                        .build(),
                )
                enableVibration(true)
                vibrationPattern = longArrayOf(0, 1000, 2000)
                description = "着信画面を開いているときの呼び出し"
            }
        )
        // 通話中: ロック画面に出る「通話中」カード
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ONGOING, "通話中", NotificationManager.IMPORTANCE_LOW).apply {
                setSound(null, null)
                description = "AIが応対している通話"
            }
        )
        // AI応対中の表示 (2026-09-19): full-screen intent で LiveCallActivity をロック画面の上に出す。
        // ⚠full-screen intent は IMPORTANCE_HIGH のチャンネルでないと発火しない。音は出さない
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_LIVE, "AI応対中の表示", NotificationManager.IMPORTANCE_HIGH).apply {
                setSound(null, null)
                enableVibration(false)
                description = "AIが応対している間、ロック画面に表示する"
            }
        )
        // 終話 (2026-09-25): AI が受けた通話が終わったら「こういう通話でした」を1枚。
        // 通知で知らせるのはこれだけ (ユーザー「終話後に出すくらいで通知は十分」)
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_ENDED, "終話", NotificationManager.IMPORTANCE_DEFAULT).apply {
                description = "AIが受けた通話の要約"
            }
        )
        // 常駐: 見張りとスタンバイの状態表示
        nm.createNotificationChannel(
            NotificationChannel(CHANNEL_WATCH, "待機", NotificationManager.IMPORTANCE_MIN).apply {
                setSound(null, null)
                description = "着信の見張り"
            }
        )
    }

    companion object {
        private const val TAG = "FragmentApp"

        const val CHANNEL_INCOMING = "incoming"
        const val CHANNEL_RING = "ring"
        const val CHANNEL_RING_ONSCREEN = "ring_onscreen"
        const val CHANNEL_ONGOING = "ongoing"
        const val CHANNEL_WATCH = "watch"
        const val CHANNEL_LIVE = "live"
        const val CHANNEL_ENDED = "ended"

        const val NOTIF_INCOMING = 1001
        const val NOTIF_ONGOING = 1002
        const val NOTIF_WATCH = 1003

        fun Application.phoneAccountHandle(): PhoneAccountHandle =
            PhoneAccountHandle(
                ComponentName(this, FragmentConnectionService::class.java),
                "fragment-self-managed"
            )
    }
}

/** Application 以外からも同じハンドルが要るので、Context から作れる形も置いておく */
fun android.content.Context.fragmentPhoneAccountHandle(): PhoneAccountHandle =
    PhoneAccountHandle(
        ComponentName(this, FragmentConnectionService::class.java),
        "fragment-self-managed"
    )
