package jp.sleeptree.fragment.ui

import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.KeyboardArrowRight
import androidx.compose.material.icons.automirrored.rounded.OpenInNew
import androidx.compose.material.icons.rounded.Check
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import jp.sleeptree.fragment.BuildConfig
import jp.sleeptree.fragment.Prefs
import jp.sleeptree.fragment.ServerState
import jp.sleeptree.fragment.WatchService
import jp.sleeptree.fragment.api.FragmentApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private val LIVE_OPTIONS = listOf(
    Triple(Prefs.LIVE_NONE, "なし", "何も出しません"),
    Triple(Prefs.LIVE_VISUALIZER, "波形", "相手の名前と声の波形"),
    Triple(Prefs.LIVE_CHAT, "会話", "会話の文字だけ"),
)

/**
 * 設定。⚠形の決まりは管制室の設定ページ (2026-09-24 整理) と同じ: 節ごとに 1 枚のカード、
 * 行は「名前 + 一行の説明」と右端の操作だけ。on/off はスイッチ。
 * ⚠プロンプト・取り次ぎ先など重い設定は PC の管制室に置いたまま (ここには出さない)
 */
@Composable
fun SettingsScreen(
    prefs: Prefs,
    paused: Boolean,
    onPaused: (Boolean) -> Unit,
    monitorOutside: Boolean,
    onMonitorOutside: (Boolean) -> Unit,
) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val mode by ServerState.answerMode.collectAsState()
    var liveDisplay by remember { mutableStateOf(prefs.liveDisplay) }
    var liveDialog by remember { mutableStateOf(false) }

    fun setMode(m: String) {
        if (m == mode) return
        ServerState.setAnswerMode(m)
        // スタンバイには切り忘れ対策の時限 (8 時間) がある。他のモードでは止める (Prefs の注記)
        if (m == "standby") prefs.startStandby() else prefs.clearStandby()
        WatchService.refresh(ctx)
        scope.launch {
            val ok = withContext(Dispatchers.IO) { FragmentApi(prefs).setAnswerMode(m) }
            if (!ok) Toast.makeText(ctx, "切り替えられませんでした", Toast.LENGTH_SHORT).show()
        }
    }

    // フラグメントが受けている番号 (2026-09-25 ユーザー「自分の番号を確認したいときってある」)。
    // 複数の回線を持てる設計なので、1つに決め打ちせず並べる
    val lines by androidx.compose.runtime.produceState<List<FragmentApi.Line>?>(null) {
        value = withContext(Dispatchers.IO) { FragmentApi(prefs).lines() }
    }

    LazyColumn(contentPadding = PaddingValues(bottom = 32.dp)) {
        item { PageTitle("設定") }

        item {
            SectionLabel("番号")
            Card(Modifier.padding(horizontal = 16.dp)) {
                val ls = lines
                when {
                    ls == null -> SettingRow(title = "読み込み中…")
                    ls.isEmpty() -> SettingRow(title = "回線がありません", desc = "管制室で回線を設定してください")
                    else -> ls.forEachIndexed { i, l ->
                        if (i > 0) RowDivider()
                        SettingRow(
                            title = l.number,
                            desc = "${l.label} · ${l.role}",
                            // 押すとコピー (人に教えるとき用)
                            onClick = {
                                val cm = ctx.getSystemService(android.content.ClipboardManager::class.java)
                                cm?.setPrimaryClip(android.content.ClipData.newPlainText("番号", l.number))
                                Toast.makeText(ctx, "コピーしました", Toast.LENGTH_SHORT).show()
                            },
                        )
                    }
                }
            }
        }

        item {
            SectionLabel("応答")
            Card(Modifier.padding(horizontal = 16.dp)) {
                MODES.forEachIndexed { i, m ->
                    if (i > 0) RowDivider()
                    SettingRow(
                        title = m.label,
                        desc = m.desc,
                        onClick = { setMode(m.key) },
                        trailing = {
                            if (mode == m.key) {
                                Icon(Icons.Rounded.Check, contentDescription = "選択中", tint = MaterialTheme.colorScheme.primary)
                            } else {
                                Spacer(Modifier.size(24.dp))
                            }
                        },
                    )
                }
            }
        }

        item {
            SectionLabel("この端末")
            Card(Modifier.padding(horizontal = 16.dp)) {
                // ⚠この端末の分だけ。他の端末を切り替える口は置かない (2026-09-24 ユーザー「事故のもと」)
                ToggleRow(
                    title = "この端末を鳴らす",
                    desc = if (paused) "鳴りません (AIの応対はそのまま)" else "着信と取り次ぎで鳴ります",
                    checked = !paused,
                    onChange = { onPaused(!it) },
                )
                RowDivider()
                ToggleRow(
                    title = "通話画面の外でも音を流す",
                    desc = "オフなら通話画面を開いたときだけ流します",
                    checked = monitorOutside,
                    onChange = onMonitorOutside,
                )
                RowDivider()
                SettingRow(
                    title = "AI応対中のロック画面",
                    desc = LIVE_OPTIONS.firstOrNull { it.first == liveDisplay }?.second ?: "なし",
                    onClick = { liveDialog = true },
                    trailing = { Chevron() },
                )
            }
        }

        item {
            SectionLabel("接続")
            Card(Modifier.padding(horizontal = 16.dp)) {
                SettingRow(
                    title = "管制室",
                    desc = Uri.parse(prefs.baseUrl).host ?: prefs.baseUrl,
                )
                RowDivider()
                SettingRow(
                    title = "プロンプトと取り次ぎ先",
                    desc = "PCの管制室で設定します",
                    onClick = {
                        runCatching {
                            ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("${prefs.baseUrl}/settings")))
                        }
                    },
                    trailing = {
                        Icon(
                            Icons.AutoMirrored.Rounded.OpenInNew,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.size(20.dp),
                        )
                    },
                )
                RowDivider()
                SettingRow(
                    title = "ペアリングし直す",
                    desc = "別の管制室につなぐとき",
                    onClick = { ctx.startActivity(Intent(ctx, SetupActivity::class.java)) },
                    trailing = { Chevron() },
                )
            }
        }

        item {
            Text(
                "フラグメント ${BuildConfig.VERSION_NAME}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 24.dp, vertical = 20.dp),
            )
        }
    }

    if (liveDialog) {
        AlertDialog(
            onDismissRequest = { liveDialog = false },
            title = { Text("AI応対中のロック画面") },
            text = {
                Column {
                    LIVE_OPTIONS.forEach { (key, label, desc) ->
                        Row(
                            Modifier
                                .fillMaxWidth()
                                .clickable {
                                    liveDisplay = key
                                    prefs.liveDisplay = key
                                    liveDialog = false
                                }
                                .padding(vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            RadioButton(selected = liveDisplay == key, onClick = null)
                            Spacer(Modifier.width(12.dp))
                            Column {
                                Text(label, style = MaterialTheme.typography.bodyLarge)
                                Text(
                                    desc,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }
                }
            },
            confirmButton = { TextButton(onClick = { liveDialog = false }) { Text("閉じる") } },
        )
    }
}

@Composable
private fun Chevron() {
    Icon(
        Icons.AutoMirrored.Rounded.KeyboardArrowRight,
        contentDescription = null,
        tint = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}
