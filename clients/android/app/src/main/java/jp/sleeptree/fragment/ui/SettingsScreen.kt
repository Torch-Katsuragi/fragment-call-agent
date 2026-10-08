package jp.sleeptree.fragment.ui

import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.automirrored.rounded.KeyboardArrowRight
import androidx.compose.material.icons.automirrored.rounded.OpenInNew
import androidx.compose.material.icons.rounded.Check
import androidx.compose.material.icons.rounded.Dialpad
import androidx.compose.material.icons.rounded.Info
import androidx.compose.material.icons.rounded.Link
import androidx.compose.material.icons.rounded.NotificationsActive
import androidx.compose.material.icons.rounded.PhoneInTalk
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.RadioButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import jp.sleeptree.fragment.BuildConfig
import jp.sleeptree.fragment.Prefs
import jp.sleeptree.fragment.ServerState
import jp.sleeptree.fragment.WatchService
import jp.sleeptree.fragment.api.FragmentApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private val LIVE_OPTIONS = listOf(
    Triple(Prefs.LIVE_NONE, "なし", "何も出しません"),
    Triple(Prefs.LIVE_VISUALIZER, "波形", "相手の名前と声の波形"),
    Triple(Prefs.LIVE_CHAT, "会話", "会話の文字だけ"),
)

/**
 * 設定 (2026-09-26 にスマホ本体の設定画面の形へ。ユーザー「設定がだいぶ煩雑になってきた」)。
 *   トップ … この端末 (名前・役割) のカード + 分類ごとの行 (アイコン・名前・いまの状態)
 *   分類   … 押すとその分類だけのページ。戻るでトップへ
 * ⚠行の形の決まりは管制室の設定ページ (2026-09-24 整理) と同じ: 節ごとに 1 枚のカード、
 *   行は「名前 + 一行の説明」と右端の操作だけ。on/off はスイッチ
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
    var accountName by remember { mutableStateOf(prefs.accountName) }
    // 開いている分類。null = トップ
    var page by rememberSaveable { mutableStateOf<String?>(null) }
    BackHandler(enabled = page != null) { page = if (page == "hours") "ring" else null }

    fun setMode(m: String) {
        if (m == mode) return
        ServerState.setAnswerMode(m)
        // ⚠スタンバイの時限 (8 時間) と時間割への戻りはサーバーが持つ (2026-09-29)
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

    // おやすみモードの例外に入っているか (着信音のチャンネルが canBypassDnd)。設定から戻ったときに映るよう数秒おきに見る
    val nm = remember { ctx.getSystemService(android.app.NotificationManager::class.java) }
    var bypassDnd by remember { mutableStateOf(true) }
    LaunchedEffect(Unit) {
        while (true) {
            bypassDnd = nm?.getNotificationChannel(jp.sleeptree.fragment.FragmentApp.CHANNEL_RING)?.canBypassDnd() ?: true
            delay(3000)
        }
    }

    val modeLabel = MODES.firstOrNull { it.key == mode }?.label ?: "…"
    val modeInfo by ServerState.modeInfo.collectAsState()
    // 自分の受付時間 (2026-09-29)。保存したら読み直す
    var hoursRev by remember { mutableStateOf(0) }
    val myHours by androidx.compose.runtime.produceState<Map<String, List<FragmentApi.Block>>?>(null, hoursRev) {
        value = withContext(Dispatchers.IO) { FragmentApi(prefs).myHours() }
    }
    val liveLabel = LIVE_OPTIONS.firstOrNull { it.first == liveDisplay }?.second ?: "なし"
    val host = Uri.parse(prefs.baseUrl).host ?: prefs.baseUrl

    LazyColumn(contentPadding = PaddingValues(bottom = 32.dp)) {
        when (page) {
            null -> {
                item { PageTitle("設定") }
                // ログインしているアカウント (2026-10-04)。ほかの端末に「〇〇が応対中」と出るのはこの名前。
                // ⚠端末は名前も役割も持たない。名前と担当は管制室のメンバー欄で決まる
                item {
                    Card(Modifier.padding(horizontal = 16.dp)) {
                        Row(Modifier.padding(18.dp), verticalAlignment = Alignment.CenterVertically) {
                            Avatar(accountName.ifEmpty { null }, size = 52.dp)
                            Spacer(Modifier.width(14.dp))
                            Column(Modifier.weight(1f)) {
                                Text(
                                    accountName.ifEmpty { "ログインしているアカウント" },
                                    style = MaterialTheme.typography.titleMedium,
                                )
                                Text(
                                    prefs.accountEmail.ifEmpty { "名前と担当は管制室のメンバー欄で決まります" },
                                    style = MaterialTheme.typography.bodySmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                        }
                    }
                }
                item {
                    Spacer(Modifier.size(12.dp))
                    Card(Modifier.padding(horizontal = 16.dp)) {
                        CategoryRow(Icons.Rounded.PhoneInTalk, "応答", modeLabel) { page = "answer" }
                        RowDivider()
                        CategoryRow(
                            Icons.Rounded.NotificationsActive,
                            "着信と表示",
                            listOf(if (paused) "鳴りません" else "鳴らす", "ロック画面: $liveLabel").joinToString(" · "),
                            warn = !bypassDnd,
                        ) { page = "ring" }
                        RowDivider()
                        CategoryRow(
                            Icons.Rounded.Dialpad,
                            "番号",
                            lines?.firstOrNull()?.number ?: if (lines == null) "読み込み中…" else "回線がありません",
                        ) { page = "lines" }
                        RowDivider()
                        CategoryRow(Icons.Rounded.Link, "接続", host) { page = "connection" }
                        RowDivider()
                        CategoryRow(Icons.Rounded.Info, "このアプリについて", "フラグメント ${BuildConfig.VERSION_NAME}") {
                            page = "about"
                        }
                    }
                }
            }

            "answer" -> {
                item { SubPageTitle("応答") { page = null } }
                item {
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
                ServerState.modeInfoText(modeInfo)?.let { t -> item { Note(t) } }
                item {
                    Note(
                        "応答モードは回線全体の設定です。時間割は PC の管制室で決めます" +
                            (if (modeInfo?.scheduleEnabled == true) " (手で切り替えると次の切り替わりまで優先)" else "") +
                            "。この端末だけ鳴らしたくないときは「着信と表示」から",
                    )
                }
            }

            "ring" -> {
                item { SubPageTitle("着信と表示") { page = null } }
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
                        // 人ごとの受付時間 (2026-09-29)。自分のログインの全端末に効く
                        SettingRow(
                            title = "受付時間",
                            desc = hoursText(myHours),
                            onClick = { if (myHours != null) page = "hours" },
                            trailing = { Chevron() },
                        )
                        RowDivider()
                        SettingRow(
                            title = "おやすみモードの例外",
                            desc = if (bypassDnd) "おやすみモード中も鳴ります"
                            else "モード → サイレント モード → アプリ に「フラグメント」を追加",
                            onClick = {
                                runCatching {
                                    // ⚠Settings.ACTION_ZEN_MODE_SETTINGS は非公開の定数。文字列は同じ (モードの一覧が開く)
                                    ctx.startActivity(Intent("android.settings.ZEN_MODE_SETTINGS").addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
                                }
                            },
                            trailing = { OpenOutside() },
                        )
                    }
                }
                item {
                    SectionLabel("通話中の音")
                    Card(Modifier.padding(horizontal = 16.dp)) {
                        ToggleRow(
                            title = "通話画面の外でも音を流す",
                            desc = "オフなら通話画面を開いたときだけ流します",
                            checked = monitorOutside,
                            onChange = onMonitorOutside,
                        )
                    }
                }
                item {
                    SectionLabel("AI応対中のロック画面")
                    Card(Modifier.padding(horizontal = 16.dp)) {
                        LIVE_OPTIONS.forEachIndexed { i, (key, label, desc) ->
                            if (i > 0) RowDivider()
                            SettingRow(
                                title = label,
                                desc = desc,
                                onClick = {
                                    liveDisplay = key
                                    prefs.liveDisplay = key
                                },
                                trailing = { RadioButton(selected = liveDisplay == key, onClick = null) },
                            )
                        }
                    }
                }
            }

            "hours" -> {
                item { SubPageTitle("受付時間") { page = "ring" } }
                item {
                    Card(Modifier.padding(horizontal = 16.dp)) {
                        HoursEditor(prefs, myHours.orEmpty()) {
                            hoursRev++
                            page = "ring"
                        }
                    }
                }
                item { Note("この時間だけ、あなたの端末が着信と取り次ぎで鳴ります。オフの日は鳴りません。祝は祝日と休業日です。保留中に名指しで呼ばれたときは時間外でも鳴ります") }
            }

            "lines" -> {
                item { SubPageTitle("番号") { page = null } }
                item {
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
                item { Note("押すと番号をコピーします") }
            }

            "connection" -> {
                item { SubPageTitle("接続") { page = null } }
                item {
                    Card(Modifier.padding(horizontal = 16.dp)) {
                        SettingRow(title = "管制室", desc = host)
                        RowDivider()
                        // Google アカウントでログインし直す (2026-09-26)
                        SettingRow(
                            title = "Google アカウントでログインし直す",
                            desc = "機種を変えたとき・別のアカウントに替えるとき",
                            onClick = {
                                val act = ctx as? android.app.Activity ?: return@SettingRow
                                scope.launch {
                                    // 古いログインを先に切る (前の管制室に端末の登録を残さない、2026-10-04)
                                    if (prefs.deviceToken.isNotEmpty()) {
                                        withContext(Dispatchers.IO) { FragmentApi(prefs).logout() }
                                    }
                                    when (val r = GoogleLogin.run(act, prefs.baseUrl, null, prefs.deviceId)) {
                                        is GoogleLogin.Result -> {
                                            GoogleLogin.save(prefs, r)
                                            accountName = prefs.accountName
                                            WatchService.refresh(ctx)
                                            Toast.makeText(ctx, "${r.email} でログインしました", Toast.LENGTH_SHORT).show()
                                        }
                                        is String -> Toast.makeText(ctx, r, Toast.LENGTH_LONG).show()
                                        else -> Unit
                                    }
                                }
                            },
                            trailing = { Chevron() },
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
                            trailing = { OpenOutside() },
                        )
                        RowDivider()
                        // ⚠管制室はアカウントで決まる (入口は 1 つ、2026-10-04 の同居構成)。ここは自前で
                        //   サーバーを立てた人向けの口なので、ふつうは使わないと書いておく
                        SettingRow(
                            title = "別のサーバーにつなぐ",
                            desc = "自分でサーバーを立てたときだけ。ふつうはアカウントで管制室が決まります",
                            onClick = {
                                scope.launch {
                                    if (prefs.deviceToken.isNotEmpty()) {
                                        withContext(Dispatchers.IO) { FragmentApi(prefs).logout() }
                                    }
                                    ctx.startActivity(Intent(ctx, SetupActivity::class.java))
                                }
                            },
                            trailing = { Chevron() },
                        )
                    }
                }
            }

            "about" -> {
                item { SubPageTitle("このアプリについて") { page = null } }
                item {
                    Card(Modifier.padding(horizontal = 16.dp)) {
                        SettingRow(title = "バージョン", desc = "フラグメント ${BuildConfig.VERSION_NAME}")
                        RowDivider()
                        SettingRow(title = "端末の識別子", desc = prefs.deviceId)
                    }
                }
            }
        }
    }

}

/** トップの分類の行。アイコンは丸い地の上に、右にいまの状態。warn = 注意が要る (おやすみモードで鳴らない等) */
@Composable
private fun CategoryRow(icon: ImageVector, title: String, summary: String, warn: Boolean = false, onClick: () -> Unit) {
    SettingRow(
        title = title,
        desc = summary,
        onClick = onClick,
        leading = {
            Box(
                Modifier
                    .size(40.dp)
                    .clip(CircleShape)
                    .background(
                        if (warn) MaterialTheme.colorScheme.errorContainer
                        else MaterialTheme.colorScheme.primaryContainer
                    ),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    icon,
                    contentDescription = null,
                    tint = if (warn) MaterialTheme.colorScheme.onErrorContainer else MaterialTheme.colorScheme.onPrimaryContainer,
                    modifier = Modifier.size(22.dp),
                )
            }
        },
        trailing = { Chevron() },
    )
}

/** 分類のページの見出し (戻るつき) */
@Composable
private fun SubPageTitle(text: String, onBack: () -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(start = 4.dp, end = 12.dp, top = 12.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = onBack) {
            Icon(Icons.AutoMirrored.Rounded.ArrowBack, contentDescription = "戻る")
        }
        Text(text, style = MaterialTheme.typography.headlineSmall)
    }
}

/** カードの下の小さな注記 */
@Composable
private fun Note(text: String) {
    Text(
        text,
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(horizontal = 28.dp, vertical = 10.dp),
    )
}

@Composable
private fun OpenOutside() {
    Icon(
        Icons.AutoMirrored.Rounded.OpenInNew,
        contentDescription = null,
        tint = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.size(20.dp),
    )
}

@Composable
private fun Chevron() {
    Icon(
        Icons.AutoMirrored.Rounded.KeyboardArrowRight,
        contentDescription = null,
        tint = MaterialTheme.colorScheme.onSurfaceVariant,
    )
}
