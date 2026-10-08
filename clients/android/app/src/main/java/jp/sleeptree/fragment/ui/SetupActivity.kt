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
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import jp.sleeptree.fragment.Prefs
import kotlinx.coroutines.launch

/**
 * 接続先の設定 = Google アカウントでのログイン (2026-09-26 に作り直した)。
 *
 * 管制室の URL を入れて「Google アカウントでログイン」→ メンバーならこの端末のログインがもらえる。
 * 初めての人は招待のメールのリンクから参加する (JoinActivity)。
 * ⚠旧式のペアリング (管制室が出す QR の合言葉・手入力のトークン・fragment://pair) はやめた
 *   (ユーザー「後方互換は完全に絶っていい」)。合言葉は持っていれば誰でも全部の権限で入れ、
 *   1 台だけ締め出すことができなかった
 * ⚠中央の振り分けサーバーは作らない方針 (2026-07-31) は同じ。接続先は各自の管制室
 */
class SetupActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = Prefs(this)
        setContent {
            FragmentTheme {
                val scope = rememberCoroutineScope()
                var url by remember { mutableStateOf(prefs.baseUrl.ifEmpty { DEFAULT_URL }) }
                var busy by remember { mutableStateOf(false) }
                var error by remember { mutableStateOf("") }
                Surface(Modifier.fillMaxSize()) {
                    Column(
                        Modifier
                            .safeDrawingPadding()
                            .padding(24.dp)
                    ) {
                        Text("ログイン", style = MaterialTheme.typography.headlineSmall)
                        Spacer(Modifier.size(8.dp))
                        Text(
                            "メンバーの Google アカウントでログインします。初めての人は、招待のメールのリンクから開いてください。",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Spacer(Modifier.size(20.dp))
                        OutlinedTextField(
                            value = url,
                            onValueChange = { url = it },
                            label = { Text("管制室の URL") },
                            singleLine = true,
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Spacer(Modifier.size(16.dp))
                        Button(
                            enabled = !busy && url.isNotBlank(),
                            onClick = {
                                busy = true
                                error = ""
                                val base = url.trim().trimEnd('/')
                                scope.launch {
                                    when (val r = GoogleLogin.run(this@SetupActivity, base, null, prefs.deviceId)) {
                                        is GoogleLogin.Result -> {
                                            GoogleLogin.save(prefs, r)
                                            startActivity(Intent(this@SetupActivity, MainActivity::class.java))
                                            finish()
                                        }
                                        is String -> error = r
                                        else -> Unit // 取り消し
                                    }
                                    busy = false
                                }
                            },
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text(if (busy) "確認しています…" else "Google アカウントでログイン") }
                        if (error.isNotEmpty()) {
                            Spacer(Modifier.size(12.dp))
                            Text(error, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
                        }
                    }
                }
            }
        }
    }

    companion object {
        const val DEFAULT_URL = "https://fragment.example.com"
    }
}
