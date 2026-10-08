package jp.sleeptree.fragment.ui

import android.content.Intent
import android.net.Uri
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
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
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
 * 招待から参加する (2026-09-26)。招待メールのリンクで開く:
 *   https://fragment.example.com/invite/<招待> … App Links (管制室の /.well-known/assetlinks.json で結んである)
 *   fragment://invite?server=<管制室>&token=<招待> … 招待のページの「アプリで開く」
 * 「Google で参加」を押す → アカウントを選ぶ → 招待したアドレスと同じなら参加できる。
 * ⚠リンクはどこからでも飛ばせるので、**どの管制室に参加するかを大きく見せ**、押すまで何もしない
 *   (偽の管制室に参加させられても、渡るのはその管制室宛ての Google の身元証明だけで、本物では使えない)
 */
class JoinActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = Prefs(this)
        val data: Uri? = intent?.data
        val (base, invite) = when {
            data == null -> null to null
            data.scheme == "https" -> "https://${data.host}" to data.lastPathSegment
            data.scheme == "fragment" -> data.getQueryParameter("server")?.trimEnd('/') to data.getQueryParameter("token")
            else -> null to null
        }
        setContent {
            FragmentTheme {
                val scope = rememberCoroutineScope()
                var busy by remember { mutableStateOf(false) }
                var error by remember { mutableStateOf("") }
                val host = base?.let { Uri.parse(it).host } ?: ""
                val switching = prefs.baseUrl.isNotEmpty() && prefs.baseUrl != base
                Surface(Modifier.fillMaxSize()) {
                    Column(
                        Modifier
                            .safeDrawingPadding()
                            .padding(24.dp)
                    ) {
                        Text("フラグメントへの招待", style = MaterialTheme.typography.headlineSmall)
                        Spacer(Modifier.size(16.dp))
                        if (base == null || invite.isNullOrEmpty() || !base.startsWith("https://")) {
                            Text("このリンクは招待のリンクではないようです", color = MaterialTheme.colorScheme.error)
                            return@Column
                        }
                        Text("参加先", style = MaterialTheme.typography.labelLarge, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text(host, style = MaterialTheme.typography.titleLarge)
                        Spacer(Modifier.size(8.dp))
                        Text(
                            "招待されたメールアドレスの Google アカウントを選んでください。",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        if (switching) {
                            Spacer(Modifier.size(8.dp))
                            Text(
                                "今つながっている管制室 (${Uri.parse(prefs.baseUrl).host}) から切り替わります",
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.error,
                            )
                        }
                        Spacer(Modifier.size(24.dp))
                        Button(
                            enabled = !busy,
                            onClick = {
                                busy = true
                                error = ""
                                scope.launch {
                                    when (val r = GoogleLogin.run(this@JoinActivity, base, invite, prefs.deviceId)) {
                                        is GoogleLogin.Result -> {
                                            GoogleLogin.save(prefs, r)
                                            startActivity(
                                                Intent(this@JoinActivity, MainActivity::class.java)
                                                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)
                                            )
                                            finish()
                                        }
                                        is String -> error = r
                                        else -> Unit // 取り消し
                                    }
                                    busy = false
                                }
                            },
                            modifier = Modifier.fillMaxWidth(),
                        ) { Text(if (busy) "確認しています…" else "Google で参加") }
                        if (error.isNotEmpty()) {
                            Spacer(Modifier.size(12.dp))
                            Text(error, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
                        }
                        Spacer(Modifier.size(8.dp))
                        TextButton(onClick = { finish() }) { Text("やめる") }
                    }
                }
            }
        }
    }
}
