package jp.sleeptree.fragment.ui

import android.app.Activity
import android.util.Log
import androidx.credentials.CredentialManager
import androidx.credentials.CustomCredential
import androidx.credentials.GetCredentialRequest
import androidx.credentials.exceptions.GetCredentialCancellationException
import com.google.android.libraries.identity.googleid.GetSignInWithGoogleOption
import com.google.android.libraries.identity.googleid.GoogleIdTokenCredential
import jp.sleeptree.fragment.Prefs
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.BufferedReader
import java.net.HttpURLConnection
import java.net.URL

/**
 * Google アカウントでのログイン (2026-09-26)。招待から参加するときと、メンバーがログインし直すときに使う。
 *
 * 流れ: 管制室の /api/public/config で Web クライアント ID を知る → それを serverClientId にして
 *   Google にログイン (アカウントを選ぶ) → 受け取った ID トークンを管制室の /api/device/join か
 *   /api/device/login に渡す → 端末ごとのログイン (fr1_…) をもらって保存する。
 * ⚠Android 側の OAuth クライアント (パッケージ名 + 署名の SHA-1) が同じ GCP プロジェクトに要る
 *   (your-gcp-project に「フラグメント (Android)」として登録済み)。署名を変えたら (Play) 登録を足す
 */
object GoogleLogin {
    private const val TAG = "GoogleLogin"

    data class Result(val token: String, val base: String, val name: String, val email: String, val role: String)

    /** @return 成功なら Result、失敗なら理由の文字列 (取り消しは null) */
    suspend fun run(activity: Activity, base: String, invite: String?, deviceId: String): Any? {
        val cfg = withContext(Dispatchers.IO) { get("$base/api/public/config") }
            ?: return "管制室 ($base) に接続できませんでした"
        val clientId = runCatching { JSONObject(cfg).getString("google_client_id") }.getOrNull()
            ?.takeIf { it.isNotEmpty() } ?: return "この管制室は Google ログインに対応していません"
        val idToken = try {
            val req = GetCredentialRequest.Builder()
                .addCredentialOption(GetSignInWithGoogleOption.Builder(clientId).build())
                .build()
            val cred = CredentialManager.create(activity).getCredential(activity, req).credential
            if (cred is CustomCredential && cred.type == GoogleIdTokenCredential.TYPE_GOOGLE_ID_TOKEN_CREDENTIAL) {
                GoogleIdTokenCredential.createFrom(cred.data).idToken
            } else {
                return "Google アカウントを受け取れませんでした"
            }
        } catch (e: GetCredentialCancellationException) {
            return null
        } catch (e: Exception) {
            Log.w(TAG, "google sign-in failed", e)
            return "Google ログインに失敗しました (${e.javaClass.simpleName})"
        }
        val body = JSONObject().put("id_token", idToken).put("device_id", deviceId)
        if (invite != null) body.put("invite", invite)
        val path = if (invite != null) "/api/device/join" else "/api/device/login"
        val (code, text) = withContext(Dispatchers.IO) { post("$base$path", body) }
        val o = runCatching { JSONObject(text ?: "") }.getOrNull()
        if (code !in 200..299 || o == null) return o?.optString("error")?.ifEmpty { null } ?: "参加できませんでした ($code)"
        val m = o.optJSONObject("member")
        return Result(
            token = o.getString("token"),
            base = o.optString("base").ifEmpty { base },
            name = m?.optString("name").orEmpty(),
            email = m?.optString("email").orEmpty(),
            role = m?.optString("role").orEmpty(),
        )
    }

    /** もらったログインを保存する。⚠プッシュの宛先は新しいログインで送り直す */
    fun save(prefs: Prefs, r: Result) {
        prefs.baseUrl = r.base
        prefs.deviceToken = r.token
        prefs.accountName = r.name
        prefs.accountEmail = r.email
        prefs.pushRegisteredToken = ""
        prefs.pushHasDeviceId = false
    }

    private fun get(url: String): String? = runCatching {
        val c = URL(url).openConnection() as HttpURLConnection
        c.connectTimeout = 5000
        c.readTimeout = 8000
        try {
            if (c.responseCode in 200..299) c.inputStream.bufferedReader().use(BufferedReader::readText) else null
        } finally {
            c.disconnect()
        }
    }.getOrNull()

    private fun post(url: String, body: JSONObject): Pair<Int, String?> = runCatching {
        val c = URL(url).openConnection() as HttpURLConnection
        c.requestMethod = "POST"
        c.connectTimeout = 5000
        c.readTimeout = 10000
        c.doOutput = true
        c.setRequestProperty("Content-Type", "application/json")
        try {
            c.outputStream.use { it.write(body.toString().toByteArray()) }
            val code = c.responseCode
            val s = (if (code in 200..299) c.inputStream else c.errorStream)?.bufferedReader()?.use(BufferedReader::readText)
            code to s
        } finally {
            c.disconnect()
        }
    }.getOrElse { -1 to null }
}
