package jp.sleeptree.fragment.call

import android.app.Notification
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import jp.sleeptree.fragment.FragmentApp
import jp.sleeptree.fragment.R
import jp.sleeptree.fragment.ui.IncomingCallActivity
import jp.sleeptree.fragment.Prefs
import jp.sleeptree.fragment.ui.LiveCallActivity
import jp.sleeptree.fragment.ui.MainActivity

object CallNotifications {

    /**
     * 着信通知。**画面も音もこれが出す** (2026-09-25、CallCoordinator.showIncomingUi の注記)。
     *
     * - ロック中・画面オフ … full-screen intent で着信画面が全画面で出る
     * - 端末を使っている最中 … OS が上部のバナーにする (普通の電話アプリと同じ)
     * - 録音告知中 … 無音のチャンネル (CHANNEL_INCOMING)、「出る」無し
     * - 告知の後 … 着信音のチャンネル (CHANNEL_RING) に出し直す。FLAG_INSISTENT で応答まで鳴り続ける
     *
     * ⚠Android 14+ では USE_FULL_SCREEN_INTENT が既定で付くのは「通話アプリ」だけ。
     *   MANAGE_OWN_CALLS を宣言し self-managed PhoneAccount を登録しているので条件を満たす。
     * ⚠CATEGORY_CALL + 相手の番号 (Person の tel: URI) を付けると、おやすみモードでは
     *   「通話を許可する相手」の設定で判定される = 普通の電話と同じ。お気に入りの連絡先なら鳴る
     */
    fun showIncoming(context: Context, inc: CallCoordinator.Incoming?, screenVisible: Boolean = false) {
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        if (inc == null) return
        fun screen(code: Int, answer: Boolean) = PendingIntent.getActivity(
            context, code,
            Intent(context, IncomingCallActivity::class.java)
                .putExtra(IncomingCallActivity.EXTRA_ANSWER, answer)
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        fun action(code: Int, act: String) = PendingIntent.getBroadcast(
            context, code,
            Intent(context, CallActionReceiver::class.java).setAction(act),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val who = inc.name ?: inc.lookup?.name ?: inc.number.ifEmpty { "着信" }
        val announcing = inc.announcing
        val isCall = inc.kind == CallCoordinator.Kind.CALL
        val b = Notification.Builder(
            context,
            when {
                announcing -> FragmentApp.CHANNEL_INCOMING
                // 着信画面が前に出ていれば、鳴らすだけでバナーは出さない
                screenVisible -> FragmentApp.CHANNEL_RING_ONSCREEN
                else -> FragmentApp.CHANNEL_RING
            },
        )
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(who)
            .setContentText(
                when {
                    announcing -> "録音告知中…"
                    isCall -> "着信 · 出なければAIが預かります"
                    else -> "AIが応対中の通話に呼ばれています"
                }
            )
            .setCategory(Notification.CATEGORY_CALL)
            .setOngoing(true)
            .setContentIntent(screen(0, false))
            .setFullScreenIntent(screen(0, false), true)
        if (inc.number.isNotEmpty()) {
            b.addPerson(android.app.Person.Builder().setName(who).setUri("tel:${inc.number}").build())
        }
        // 3択 (2026-09-25)。取り次ぎは AI が既に話しているので「切る」は無い
        if (isCall) {
            b.addAction(Notification.Action.Builder(null, "切る", action(2, CallActionReceiver.ACTION_REJECT)).build())
        }
        b.addAction(
            Notification.Action.Builder(
                null,
                if (isCall) "AIに任せる" else "AIに続けさせる",
                action(3, CallActionReceiver.ACTION_TO_AI),
            ).build()
        )
        // 「出る」は告知の後。⚠画面を直接開く (BroadcastReceiver 経由は Android 12+ で禁止)
        if (!announcing) {
            b.addAction(Notification.Action.Builder(null, "出る", screen(1, true)).build())
        }
        val n = b.build()
        if (!announcing) n.flags = n.flags or Notification.FLAG_INSISTENT
        nm.notify(FragmentApp.NOTIF_INCOMING, n)
    }

    fun cancelIncoming(context: Context) {
        context.getSystemService(NotificationManager::class.java)
            ?.cancel(FragmentApp.NOTIF_INCOMING)
    }

    /**
     * 「通話中」カード。AIが応対している間ロック画面に出す (要件3)。
     *
     * ⚠CATEGORY_CALL + ongoing でロック画面に載る。Notification.CallStyle は API 31+ なので、
     *   Android 10 のメイン端末では使えない。見た目を揃えるのは端末が新しくなってから
     */
    fun showOngoing(
        context: Context,
        callId: String,
        who: String,
        liveDisplay: String = Prefs.LIVE_NONE,
        answered: Boolean = false,
    ) {
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        val open = PendingIntent.getActivity(
            context, 3,
            MainActivity.callIntentById(context, callId).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        // 「AI応対中の表示」あり → full-screen intent で LiveCallActivity を出す (ロック中・画面OFFのとき)。
        //   ⚠常駐サービスから直接 startActivity するとバックグラウンド起動として止められるので、
        //     通知の full-screen intent に載せる。チャンネルは IMPORTANCE_HIGH が要る (FragmentApp)
        val live = !answered && liveDisplay != Prefs.LIVE_NONE
        val b = Notification.Builder(context, if (live) FragmentApp.CHANNEL_LIVE else FragmentApp.CHANNEL_ONGOING)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("通話中 · $who")
            .setContentText(if (answered) "あなたが応対しています" else "AIが応対しています")
            .setCategory(Notification.CATEGORY_CALL)
            .setOngoing(true)
            .setContentIntent(open)
            // ⚠ロック画面には出さない (2026-09-19 ユーザー「自動応答はサイレントでバックグラウンドで」)。
            //   AIが受けている間、端末は何も見せない。ロックを解けば通知シェードにはある
            .setVisibility(Notification.VISIBILITY_SECRET)
        if (answered) {
            // 自分が出ている通話を切る口 (2026-09-24 ユーザー「スマホから通話を切る方法がわからん」)。
            //   通話画面の下のバーにもあるが、画面を離れても切れるようにここにも置く
            val hang = PendingIntent.getBroadcast(
                context, 5,
                Intent(context, CallActionReceiver::class.java).setAction(CallActionReceiver.ACTION_HANGUP),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            b.addAction(Notification.Action.Builder(null, "通話終了", hang).build())
        }
        if (live) {
            val fsi = PendingIntent.getActivity(
                context, 4,
                Intent(context, LiveCallActivity::class.java)
                    .putExtra(LiveCallActivity.EXTRA_CALL_ID, callId)
                    .putExtra(LiveCallActivity.EXTRA_MODE, liveDisplay)
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
            )
            b.setFullScreenIntent(fsi, true)
        }
        nm.notify(FragmentApp.NOTIF_ONGOING, b.build())
    }

    /**
     * 終話通知 (2026-09-25)。AI が受けた通話が終わったら「誰から・何の用だったか」を1枚出す。
     * 押すとその通話の記録を開く。通話ごとに別の通知にする (続けて何件か来ても上書きしない)。
     * ⚠ロック画面では中身を伏せる (要約に用件が入る)。解除すれば読める
     */
    fun showEnded(context: Context, e: jp.sleeptree.fragment.api.FragmentApi.Ended) {
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        val open = PendingIntent.getActivity(
            context, e.id.hashCode(),
            MainActivity.callIntentById(context, e.id).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        val who = e.name ?: e.number.ifEmpty { "非通知" }
        val dur = if (e.durationSec >= 60) "${e.durationSec / 60}分${e.durationSec % 60}秒" else "${e.durationSec}秒"
        val body = e.summary.ifEmpty { "AIが応対しました" }
        val public = Notification.Builder(context, FragmentApp.CHANNEL_ENDED)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("AIが通話を受けました")
            .build()
        val n = Notification.Builder(context, FragmentApp.CHANNEL_ENDED)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("$who · $dur")
            .setContentText(body)
            .setStyle(Notification.BigTextStyle().bigText(body))
            .setContentIntent(open)
            .setAutoCancel(true)
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setPublicVersion(public)
            .build()
        nm.notify(NOTIF_ENDED_BASE + (e.id.hashCode() and 0xffff), n)
    }

    private const val NOTIF_ENDED_BASE = 20000

    fun cancelOngoing(context: Context) {
        context.getSystemService(NotificationManager::class.java)
            ?.cancel(FragmentApp.NOTIF_ONGOING)
    }
}
