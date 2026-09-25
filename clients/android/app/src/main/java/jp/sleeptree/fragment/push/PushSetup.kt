package jp.sleeptree.fragment.push

import android.content.Context
import android.util.Log
import com.google.android.gms.tasks.Tasks
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessaging
import jp.sleeptree.fragment.Prefs
import jp.sleeptree.fragment.api.FragmentApi
import org.json.JSONObject

/**
 * FCM (プッシュ) の準備。接続情報の取得 → Firebase 初期化 → トークン登録 (2026-09-18)。
 *
 * ⚠**google-services.json をアプリに焼かない**。Firebase プロジェクトは self-host ごとに違うので、
 *   接続情報 (projectId / senderId / appId / apiKey) は**管制室が配る** (/api/device/config)。
 *   1つのビルドでどのサーバーにも繋げる、が専用アプリの前提 (QR1枚で設定が終わる)。
 *   だから Gradle の google-services プラグインも使わず、FirebaseOptions で手で初期化する。
 *
 * ⚠プッシュは「起きろ」だけ。番号も理由も載っていない。起きたら今までどおり
 *   /api/device/state を取りに行く (PushService)。判断も内容もサーバーのまま。
 *
 * ⚠サーバーが FCM 無し (firebase=null) なら何もしない = ロングポーリングのみで今までどおり動く。
 */
object PushSetup {
    private const val TAG = "PushSetup"

    enum class Result { DONE, NO_SERVER_PUSH, RETRY }

    /**
     * 保存済みの接続情報で Firebase を初期化する。
     * ⚠プロセス起動時 (FragmentApp) にも呼ぶ — プッシュで起こされたプロセスは、
     *   PushService が動く前にここを通る必要がある
     */
    fun initFirebase(context: Context, prefs: Prefs): Boolean {
        if (FirebaseApp.getApps(context).isNotEmpty()) return true
        val json = prefs.firebaseOptions
        if (json.isEmpty()) return false
        return try {
            val o = JSONObject(json)
            val opts = FirebaseOptions.Builder()
                .setProjectId(o.getString("projectId"))
                .setGcmSenderId(o.getString("senderId"))
                .setApplicationId(o.getString("appId"))
                .setApiKey(o.getString("apiKey"))
                .build()
            FirebaseApp.initializeApp(context, opts)
            true
        } catch (e: Exception) {
            Log.w(TAG, "initFirebase failed", e)
            false
        }
    }

    /** 接続情報が無ければ管制室から取り、初期化し、トークンを登録する。⚠ネットワークを使うので IO で呼ぶ */
    fun ensureRegistered(context: Context, prefs: Prefs, api: FragmentApi): Result {
        if (prefs.firebaseOptions.isEmpty()) {
            val cfg = api.deviceConfig() ?: return Result.RETRY
            val fb = cfg.optJSONObject("firebase") ?: return Result.NO_SERVER_PUSH
            prefs.firebaseOptions = fb.toString()
        }
        if (!initFirebase(context, prefs)) {
            // 壊れた接続情報は捨てて取り直す
            prefs.firebaseOptions = ""
            return Result.RETRY
        }
        val token = try {
            Tasks.await(FirebaseMessaging.getInstance().token)
        } catch (e: Exception) {
            // Play開発者サービスが無い端末など。ロングポーリングで動くので致命ではない
            Log.w(TAG, "FCM token unavailable: ${e.message}")
            return Result.RETRY
        }
        return if (register(prefs, api, token)) Result.DONE else Result.RETRY
    }

    /** トークンを管制室に登録する (同じものは送らない)。onNewToken からも呼ぶ */
    fun register(prefs: Prefs, api: FragmentApi, token: String): Boolean {
        if (token.isEmpty()) return false
        if (token == prefs.pushRegisteredToken) return true
        if (!api.registerPush(token)) return false
        prefs.pushRegisteredToken = token
        Log.i(TAG, "push token registered")
        return true
    }
}
