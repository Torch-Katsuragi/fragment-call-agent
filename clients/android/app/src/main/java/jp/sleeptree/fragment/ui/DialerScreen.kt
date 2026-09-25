package jp.sleeptree.fragment.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.Backspace
import androidx.compose.material.icons.rounded.Call
import androidx.compose.material.icons.rounded.Close
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import jp.sleeptree.fragment.Prefs
import jp.sleeptree.fragment.api.FragmentApi
import jp.sleeptree.fragment.audio.CallAudio
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 発信 (2026-07-19 の管制室の発信キーパッドと同じ流れ)。自分が話し、AI は書記で支援する。
 * ⚠ブラステル経由の実発信 = 通話料がかかる。発信前に必ず確認を挟む
 * 相手が出て通話行が現れたら、自分が話す (operator) で通話画面へ移る
 */
@Composable
fun DialerScreen(prefs: Prefs, initial: String, onClose: () -> Unit, onConnected: (String) -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val api = remember(prefs) { FragmentApi(prefs) }
    var number by remember { mutableStateOf(initial.filter { it.isDigit() || it == '+' || it == '*' || it == '#' }) }
    var confirm by remember { mutableStateOf(false) }
    // 発信を受け付けた番号。null = まだかけていない
    var calling by remember { mutableStateOf<String?>(null) }
    var status by remember { mutableStateOf<FragmentApi.Dialing?>(null) }
    var error by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(calling) {
        val target = calling ?: return@LaunchedEffect
        while (true) {
            val (d, a) = withContext(Dispatchers.IO) { api.dialing() to api.deviceState()?.activeCall }
            if (d != null) status = d
            if (a != null && sameNumber(a.number, target)) {
                CallAudio.setOperator(ctx, a.room, true)
                onConnected(a.id)
                return@LaunchedEffect
            }
            if (d?.status == "failed") {
                error = d.reason.ifEmpty { "つながりませんでした" }
                calling = null
                return@LaunchedEffect
            }
            delay(1000)
        }
    }

    Column(
        Modifier
            .fillMaxSize()
            .systemBarsPadding(),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Row(Modifier.fillMaxWidth().padding(4.dp)) {
            IconButton(onClick = onClose) { Icon(Icons.Rounded.Close, contentDescription = "閉じる") }
        }

        if (calling != null) {
            // 呼び出し中
            Spacer(Modifier.weight(1f))
            Avatar(null, size = 88.dp)
            Spacer(Modifier.height(20.dp))
            Text(formatNumber(calling!!), style = MaterialTheme.typography.headlineMedium)
            Spacer(Modifier.height(8.dp))
            Text(
                if (status?.status == "answered") "つないでいます…" else "呼び出しています…",
                style = MaterialTheme.typography.bodyLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.height(6.dp))
            Text(
                "相手が出たら通話画面に移ります",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.weight(1.4f))
            return@Column
        }

        Spacer(Modifier.weight(1f))
        Text(
            if (number.isEmpty()) " " else formatNumber(number),
            fontSize = if (number.length > 13) 28.sp else 36.sp,
            style = MaterialTheme.typography.headlineMedium,
            textAlign = TextAlign.Center,
            maxLines = 1,
            modifier = Modifier.padding(horizontal = 24.dp),
        )
        Text(
            error ?: " ",
            style = MaterialTheme.typography.bodySmall,
            color = LocalSemantic.current.danger,
            modifier = Modifier.padding(top = 8.dp),
        )
        Spacer(Modifier.weight(0.6f))

        val keys = listOf(
            listOf("1", "2", "3"),
            listOf("4", "5", "6"),
            listOf("7", "8", "9"),
            listOf("*", "0", "#"),
        )
        Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
            keys.forEach { row ->
                Row(horizontalArrangement = Arrangement.spacedBy(24.dp)) {
                    row.forEach { k ->
                        Key(k) {
                            error = null
                            if (number.length < 20) number += k
                        }
                    }
                }
            }
        }
        Spacer(Modifier.height(24.dp))
        Row(verticalAlignment = Alignment.CenterVertically) {
            Spacer(Modifier.size(72.dp))
            Spacer(Modifier.width(24.dp))
            Surface(
                onClick = { confirm = true },
                enabled = number.length >= 4,
                shape = CircleShape,
                color = LocalSemantic.current.me,
                modifier = Modifier
                    .size(72.dp)
                    .alpha(if (number.length >= 4) 1f else 0.4f),
            ) {
                Box(contentAlignment = Alignment.Center) {
                    Icon(Icons.Rounded.Call, contentDescription = "発信", tint = Color.White, modifier = Modifier.size(30.dp))
                }
            }
            Spacer(Modifier.width(24.dp))
            Box(Modifier.size(72.dp), contentAlignment = Alignment.Center) {
                if (number.isNotEmpty()) {
                    IconButton(onClick = { number = number.dropLast(1) }) {
                        Icon(
                            Icons.AutoMirrored.Rounded.Backspace,
                            contentDescription = "1文字消す",
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
        Spacer(Modifier.height(32.dp))
    }

    if (confirm) {
        AlertDialog(
            onDismissRequest = { confirm = false },
            title = { Text("${formatNumber(number)} に発信しますか？") },
            text = { Text("通話料がかかります。あなたが話し、AIは書記として記録します。") },
            confirmButton = {
                TextButton(onClick = {
                    confirm = false
                    val n = number
                    scope.launch {
                        val err = withContext(Dispatchers.IO) { api.dial(n) }
                        if (err == null) calling = n else error = err
                    }
                }) { Text("発信") }
            },
            dismissButton = { TextButton(onClick = { confirm = false }) { Text("やめる") } },
        )
    }
}

@Composable
private fun Key(label: String, onClick: () -> Unit) {
    Surface(
        onClick = onClick,
        shape = CircleShape,
        color = MaterialTheme.colorScheme.surfaceContainerHigh,
        modifier = Modifier.size(72.dp),
    ) {
        Box(contentAlignment = Alignment.Center) {
            Text(label, fontSize = 28.sp, style = MaterialTheme.typography.headlineSmall)
        }
    }
}

/** 下 9 桁で比べる (+81 と 0 始まりの違い・ハイフンを吸収する) */
private fun sameNumber(a: String, b: String): Boolean {
    val x = a.filter { it.isDigit() }.takeLast(9)
    val y = b.filter { it.isDigit() }.takeLast(9)
    return x.isNotEmpty() && x == y
}

/** 日本の番号だけ見やすく区切る。携帯 090-1234-5678 / 050 / フリーダイヤル。他はそのまま */
fun formatNumber(n: String): String {
    if (n.length == 11 && Regex("^0[5789]0").containsMatchIn(n)) return "${n.take(3)}-${n.substring(3, 7)}-${n.substring(7)}"
    if (n.length == 10 && (n.startsWith("0120") || n.startsWith("0800"))) return "${n.take(4)}-${n.substring(4, 7)}-${n.substring(7)}"
    return n
}
