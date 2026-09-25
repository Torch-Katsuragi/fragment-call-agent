package jp.sleeptree.fragment.ui

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.google.mlkit.vision.barcode.common.Barcode
import com.google.mlkit.vision.codescanner.GmsBarcodeScannerOptions
import com.google.mlkit.vision.codescanner.GmsBarcodeScanning
import jp.sleeptree.fragment.Prefs
import org.json.JSONObject

/**
 * 接続先の設定。
 *
 * ⚠**中央の振り分けサーバーは作らない**方針 (2026-07-31)。接続先URLと端末トークンは
 *   管制室が出すQRに両方入っているので、QRを読むだけで設定が終わる。
 *   手入力欄はQRが読めないときの逃げ道 (と、開発中の入力口) として残してある。
 *
 * ⚠QRの中身は**管制室に入れる鍵そのもの**。スクリーンショットに残さない・他人に見せない。
 *   失効は管制室側の DEVICE_TOKEN_VERSION を変えて再起動 (PairDevice.tsx の注記)。
 */
class SetupActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = Prefs(this)
        // deep link `fragment://pair?url=...&token=...` で来たら欄に**入れるだけ** (2026-09-18)。
        //
        // ⚠保存はしない。QR は本人がカメラを向けた時点で意思が明らかなので自動保存しているが、
        //   deep link は任意のアプリやページから飛ばせるため、偽の接続先とトークンを黙って
        //   飲み込むと管制室ごと乗っ取られる。「保存して開く」を本人が押す一手を残す。
        //
        // なぜ要るか: adb の `input text` は端末の IME を通るので、日本語入力が有効な端末では
        //   トークンが「えｙJuIjoi…」「HTTPS：／／fragment。」のように化ける (Pixel 11 Pro Fold で実測)。
        //   IME を介さず値を渡す口が無いと、実機での検証を毎回カメラで QR を読ませる手作業に頼ることになる。
        //   QR の中身 ({url, token}) と同じ情報なので、将来 QR にこの URI を載せる形にも寄せられる。
        val link = intent?.data?.takeIf { it.scheme == "fragment" && it.host == "pair" }
        val linkUrl = link?.getQueryParameter("url")?.trim()?.trimEnd('/').orEmpty()
        val linkToken = link?.getQueryParameter("token")?.trim().orEmpty()
        setContent {
            FragmentTheme {
                SetupScreen(
                    initialUrl = linkUrl.ifEmpty { prefs.baseUrl },
                    initialToken = linkToken.ifEmpty { prefs.deviceToken },
                    onScan = { onOk, onErr -> scanQr(onOk, onErr) },
                    onSave = { url, token ->
                        prefs.baseUrl = url
                        prefs.deviceToken = token
                        startActivity(Intent(this, MainActivity::class.java))
                        finish()
                    },
                )
            }
        }
    }

    /**
     * ペアリングQRを読む。
     *
     * ⚠スキャンUIはPlay開発者サービス側で動くので、こちらにカメラ権限もプレビュー実装も要らない。
     *   代わりに**モジュールが端末に無いと初回は配信待ち**になる (ネットが要る)。
     *   落ちたときは黙らせず、手入力に誘導する — ここで詰まると何も始まらない画面なので。
     */
    private fun scanQr(onOk: (String, String) -> Unit, onErr: (String) -> Unit) {
        val options = GmsBarcodeScannerOptions.Builder()
            .setBarcodeFormats(Barcode.FORMAT_QR_CODE)
            .enableAutoZoom() // 管制室の画面が小さくても寄れる
            .build()
        GmsBarcodeScanning.getClient(this, options).startScan()
            .addOnSuccessListener { code ->
                val raw = code.rawValue
                if (raw.isNullOrBlank()) {
                    onErr("QRを読めませんでした")
                    return@addOnSuccessListener
                }
                // 管制室が出す形式は {"url":..., "token":..., "name":...} (PairDevice.tsx)
                val o = runCatching { JSONObject(raw) }.getOrNull()
                val url = o?.optString("url").orEmpty().trim().trimEnd('/')
                val token = o?.optString("token").orEmpty().trim()
                if (url.isEmpty() || token.isEmpty()) {
                    // ⚠他のQR (URLだけ、Wi-Fi設定など) を読んだとき。黙って握り潰さない
                    onErr("このQRはペアリング用ではないようです")
                    return@addOnSuccessListener
                }
                onOk(url, token)
            }
            .addOnCanceledListener { onErr("") }
            .addOnFailureListener { e ->
                onErr("読み取りを起動できませんでした (${e.message ?: "不明"})。手入力してください")
            }
    }
}

@Composable
private fun SetupScreen(
    initialUrl: String,
    initialToken: String,
    onScan: (onOk: (String, String) -> Unit, onErr: (String) -> Unit) -> Unit,
    onSave: (String, String) -> Unit,
) {
    var url by remember { mutableStateOf(initialUrl) }
    var token by remember { mutableStateOf(initialToken) }
    var error by remember { mutableStateOf("") }
    var scanned by remember { mutableStateOf(false) }

    Surface(Modifier.fillMaxSize()) {
        // ⚠safeDrawingPadding が無いと見出しが時刻・電池表示に潜り込む (他の画面と同じ)
        Column(
            Modifier
                .safeDrawingPadding()
                .padding(24.dp)
        ) {
            Text("接続先の設定", style = MaterialTheme.typography.headlineSmall)
            Spacer(Modifier.size(8.dp))
            Text(
                "管制室の設定画面でQRを表示して、下のボタンで読み取ってください。",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.size(20.dp))
            Button(
                onClick = {
                    error = ""
                    onScan(
                        { u, t ->
                            // ⚠読めたらそのまま保存して進む。QRには接続先も鍵も入っているので、
                            //   ここで確認ボタンを挟んでも本人に確かめようがない (手を増やすだけ)
                            url = u
                            token = t
                            scanned = true
                            onSave(u, t)
                        },
                        { msg -> error = msg },
                    )
                },
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("QRを読み取る")
            }
            if (scanned) {
                Spacer(Modifier.size(8.dp))
                Text(
                    "読み取りました: $url",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
            if (error.isNotEmpty()) {
                Spacer(Modifier.size(8.dp))
                Text(
                    error,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }

            Spacer(Modifier.size(28.dp))
            Text(
                "QRが読めないとき (手入力)",
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.size(12.dp))
            OutlinedTextField(
                value = url,
                onValueChange = { url = it },
                label = { Text("管制室のURL") },
                placeholder = { Text("https://fragment.example.com") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.size(16.dp))
            OutlinedTextField(
                value = token,
                onValueChange = { token = it },
                label = { Text("端末トークン") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth(),
            )
            Spacer(Modifier.size(24.dp))
            OutlinedButton(
                onClick = { onSave(url.trim().trimEnd('/'), token.trim()) },
                enabled = url.isNotBlank() && token.isNotBlank(),
                modifier = Modifier.fillMaxWidth(),
            ) {
                Text("保存して開く")
            }
        }
    }
}
