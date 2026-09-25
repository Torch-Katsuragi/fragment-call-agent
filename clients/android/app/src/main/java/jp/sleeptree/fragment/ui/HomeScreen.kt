package jp.sleeptree.fragment.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.KeyboardArrowRight
import androidx.compose.material.icons.rounded.Dialpad
import androidx.compose.material.icons.rounded.Headphones
import androidx.compose.material.icons.rounded.Mic
import androidx.compose.material.icons.rounded.NotificationsActive
import androidx.compose.material.icons.rounded.NotificationsOff
import androidx.compose.material.icons.rounded.PhoneInTalk
import androidx.compose.material.icons.rounded.SmartToy
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.State
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.produceState
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import jp.sleeptree.fragment.Prefs
import jp.sleeptree.fragment.ServerState
import jp.sleeptree.fragment.api.FragmentApi
import jp.sleeptree.fragment.audio.CallAudio
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext

/** 一定間隔で取り直す値。取れなかった回は前の値のまま */
@Composable
fun <T> rememberPolled(key: Any?, intervalMs: Long, fetch: () -> T?): State<T?> =
    produceState<T?>(null, key) {
        while (true) {
            withContext(Dispatchers.IO) { runCatching { fetch() }.getOrNull() }?.let { value = it }
            delay(intervalMs)
        }
    }

/** 応答モードの見せ方。⚠説明は管制室 (AnswerModeControl) と同じ文言にそろえる */
data class ModeInfo(val key: String, val label: String, val desc: String, val icon: ImageVector)

val MODES = listOf(
    ModeInfo("away", "不在", "すぐにAIが出ます", Icons.Rounded.SmartToy),
    ModeInfo("standby", "スタンバイ", "数秒鳴らし、出なければAIが出ます", Icons.Rounded.NotificationsActive),
    ModeInfo("manual", "自分で出る", "AIは出ません。出るまで鳴らします", Icons.Rounded.PhoneInTalk),
)

@Composable
fun HomeScreen(
    prefs: Prefs,
    paused: Boolean,
    onResume: () -> Unit,
    onOpenCall: (String) -> Unit,
    onOpenHistory: () -> Unit,
    onOpenSettings: () -> Unit,
    onDial: () -> Unit,
) {
    val api = FragmentApi(prefs)
    val calls by rememberPolled(Unit, 2500) { api.calls(12) }
    val mode by ServerState.answerMode.collectAsState()
    val activeState by ServerState.activeCall.collectAsState()

    val list = calls.orEmpty()
    val activeCall = list.firstOrNull { it.active }
    val recent = list.filter { !it.active }.take(5)

    Box(Modifier.fillMaxSize()) {
        LazyColumn(contentPadding = PaddingValues(bottom = 96.dp)) {
            item { PageTitle("フラグメント") }
            item {
                // ⚠ここでは切り替えない (2026-09-19 ユーザー「設定にあるなら管制室で応答モードは
                //   設定しなくていい」)。いまのモードを見せて、押したら設定へ
                ModeCard(mode, onClick = onOpenSettings, modifier = Modifier.padding(horizontal = 16.dp))
            }
            if (paused) {
                item {
                    PausedCard(onResume, Modifier.padding(start = 16.dp, end = 16.dp, top = 10.dp))
                }
            }
            item { DndCard(Modifier.padding(start = 16.dp, end = 16.dp, top = 10.dp)) }
            if (activeCall != null) {
                item {
                    SectionLabel("通話中")
                    ActiveCallCard(activeCall, onOpenCall, Modifier.padding(horizontal = 16.dp))
                }
            } else if (activeState != null) {
                // 一覧の取り直しを待つ間 (常駐サービスの方が先に知る)
                item {
                    SectionLabel("通話中")
                    Card(Modifier.padding(horizontal = 16.dp), onClick = { onOpenCall(activeState!!.id) }) {
                        Row(Modifier.padding(18.dp), verticalAlignment = Alignment.CenterVertically) {
                            LiveDot()
                            Spacer(Modifier.width(10.dp))
                            Text(activeState!!.name ?: activeState!!.number, style = MaterialTheme.typography.titleMedium)
                        }
                    }
                }
            }
            item {
                SectionLabel("最近の通話") {
                    TextButton(onClick = onOpenHistory) { Text("すべて") }
                }
            }
            item {
                Card(Modifier.padding(horizontal = 16.dp)) {
                    if (calls == null) {
                        Text(
                            "読み込み中…",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(18.dp),
                        )
                    } else if (recent.isEmpty()) {
                        Text(
                            "まだ通話はありません",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(18.dp),
                        )
                    }
                    recent.forEachIndexed { i, c ->
                        if (i > 0) RowDivider()
                        CallRow(c) { onOpenCall(c.id) }
                    }
                }
            }
        }
        ExtendedFloatingActionButton(
            onClick = onDial,
            icon = { Icon(Icons.Rounded.Dialpad, contentDescription = null) },
            text = { Text("発信") },
            containerColor = MaterialTheme.colorScheme.primary,
            contentColor = MaterialTheme.colorScheme.onPrimary,
            modifier = Modifier
                .align(Alignment.BottomEnd)
                .padding(20.dp),
        )
    }
}

@Composable
private fun ModeCard(mode: String?, onClick: () -> Unit, modifier: Modifier = Modifier) {
    val m = MODES.firstOrNull { it.key == mode }
    Card(modifier, onClick = onClick) {
        Row(Modifier.padding(horizontal = 18.dp, vertical = 16.dp), verticalAlignment = Alignment.CenterVertically) {
            Box(
                Modifier
                    .size(44.dp)
                    .clip(CircleShape)
                    .background(MaterialTheme.colorScheme.primaryContainer),
                contentAlignment = Alignment.Center,
            ) {
                Icon(
                    m?.icon ?: Icons.Rounded.SmartToy,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.onPrimaryContainer,
                )
            }
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text(m?.label ?: "…", style = MaterialTheme.typography.titleMedium)
                Text(
                    m?.desc ?: "サーバーに接続しています",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            Icon(
                Icons.AutoMirrored.Rounded.KeyboardArrowRight,
                contentDescription = "応答モードを変える",
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

/**
 * おやすみモードの例外の案内 (2026-09-25)。
 *
 * ⚠着信は通知 (CATEGORY_CALL) で鳴らすので、おやすみモード中は「通話を許可する相手」で判定される
 *   (既定はお気に入りの連絡先だけ)。知らない番号の着信も鳴らすには、フラグメントを
 *   モードの例外アプリに入れてもらうしかない。Android 16 ではチャンネル単位のスイッチが無く、
 *   「モード → サイレント モード → アプリ」から足す (Pixel 9 で確認)。
 * ⚠例外に入ると着信音のチャンネルが canBypassDnd になる。それを見て消す。
 *   設定画面から戻ってきたときに消えるよう、表示中は数秒おきに見直す
 */
@Composable
private fun DndCard(modifier: Modifier = Modifier) {
    val ctx = LocalContext.current
    val nm = remember { ctx.getSystemService(android.app.NotificationManager::class.java) }
    var bypass by remember { mutableStateOf(true) }
    LaunchedEffect(Unit) {
        while (true) {
            bypass = nm?.getNotificationChannel(jp.sleeptree.fragment.FragmentApp.CHANNEL_RING)
                ?.canBypassDnd() ?: true
            kotlinx.coroutines.delay(3000)
        }
    }
    if (bypass) return
    Card(modifier, color = MaterialTheme.colorScheme.surfaceContainerHigh) {
        Row(Modifier.padding(start = 18.dp, end = 8.dp, top = 10.dp, bottom = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(
                Icons.Rounded.NotificationsOff,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(20.dp),
            )
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text("おやすみモード中は、知らない番号の着信が鳴りません", style = MaterialTheme.typography.bodyMedium)
                Text(
                    "モード → サイレント モード → アプリ に「フラグメント」を追加",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            TextButton(onClick = {
                runCatching {
                    ctx.startActivity(
                        // ⚠Settings.ACTION_ZEN_MODE_SETTINGS は非公開の定数。文字列は同じ (モードの一覧が開く)
                        android.content.Intent("android.settings.ZEN_MODE_SETTINGS")
                            .addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
                    )
                }
            }) { Text("設定") }
        }
    }
}

@Composable
private fun PausedCard(onResume: () -> Unit, modifier: Modifier = Modifier) {
    Card(modifier, color = MaterialTheme.colorScheme.surfaceContainerHigh) {
        Row(Modifier.padding(start = 18.dp, end = 8.dp, top = 6.dp, bottom = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(
                Icons.Rounded.NotificationsOff,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(20.dp),
            )
            Spacer(Modifier.width(12.dp))
            Text("この端末は鳴りません", style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
            TextButton(onClick = onResume) { Text("鳴らす") }
        }
    }
}

@Composable
private fun ActiveCallCard(call: FragmentApi.Call, onOpenCall: (String) -> Unit, modifier: Modifier = Modifier) {
    val ctx = LocalContext.current
    val sem = LocalSemantic.current
    val audio by CallAudio.state.collectAsState()
    val mine = audio.room == call.room && audio.operator
    Card(modifier, onClick = { onOpenCall(call.id) }) {
        Column(Modifier.padding(18.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                LiveDot()
                Spacer(Modifier.width(8.dp))
                Elapsed(call.startedAt, color = sem.live)
                Spacer(Modifier.weight(1f))
                HandlerPill(mine)
            }
            Spacer(Modifier.height(14.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Avatar(call.name, size = 52.dp)
                Spacer(Modifier.width(14.dp))
                Column {
                    Text(call.displayName, style = MaterialTheme.typography.titleLarge)
                    if (call.name != null && call.number != null) {
                        Text(
                            call.number,
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
            val lines = call.segments.filter { it.speaker != "whisper" }.takeLast(2)
            if (lines.isNotEmpty()) {
                Spacer(Modifier.height(14.dp))
                Surface(color = MaterialTheme.colorScheme.surfaceContainer, shape = RoundedCornerShape(14.dp)) {
                    Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        lines.forEach { s ->
                            Row {
                                Text(
                                    speakerLabel(s.speaker),
                                    style = MaterialTheme.typography.labelMedium,
                                    color = speakerColor(s.speaker),
                                    modifier = Modifier.width(44.dp),
                                )
                                Text(
                                    s.text,
                                    style = MaterialTheme.typography.bodySmall,
                                    maxLines = 2,
                                    overflow = TextOverflow.Ellipsis,
                                )
                            }
                        }
                    }
                }
            }
            Spacer(Modifier.height(14.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedButton(onClick = { onOpenCall(call.id) }, modifier = Modifier.weight(1f)) {
                    Icon(Icons.Rounded.Headphones, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("聞く")
                }
                if (!mine) {
                    Button(
                        onClick = {
                            CallAudio.setOperator(ctx, call.room, true)
                            onOpenCall(call.id)
                        },
                        modifier = Modifier.weight(1f),
                        colors = ButtonDefaults.buttonColors(containerColor = sem.me),
                    ) {
                        Icon(Icons.Rounded.Mic, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(8.dp))
                        Text("自分が出る")
                    }
                }
            }
        }
    }
}

/** いま誰が話しているか (AI / あなた) の札 */
@Composable
fun HandlerPill(mine: Boolean) {
    val sem = LocalSemantic.current
    Surface(
        shape = RoundedCornerShape(50),
        color = if (mine) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceContainerHigh,
    ) {
        Row(Modifier.padding(horizontal = 10.dp, vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
            Icon(
                if (mine) Icons.Rounded.Mic else Icons.Rounded.SmartToy,
                contentDescription = null,
                modifier = Modifier.size(14.dp),
                tint = if (mine) sem.me else MaterialTheme.colorScheme.onSurfaceVariant,
            )
            Spacer(Modifier.width(5.dp))
            Text(
                if (mine) "あなたが応対中" else "AIが応対中",
                style = MaterialTheme.typography.labelMedium,
                color = if (mine) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

fun speakerLabel(s: String) = when (s) {
    "caller" -> "相手"
    "ai" -> "AI"
    "user" -> "あなた"
    "whisper" -> "耳打ち"
    else -> s
}

@Composable
fun speakerColor(s: String) = when (s) {
    "caller" -> LocalSemantic.current.caller
    "user" -> LocalSemantic.current.me
    else -> MaterialTheme.colorScheme.onSurfaceVariant
}

@Composable
fun HistoryScreen(prefs: Prefs, onOpenCall: (String) -> Unit) {
    val api = FragmentApi(prefs)
    val calls by rememberPolled(Unit, 5000) { api.calls(100) }
    val groups = calls.orEmpty().groupBy { localDate(it.startedAt) }.toList()

    LazyColumn(Modifier.fillMaxWidth(), contentPadding = PaddingValues(bottom = 24.dp)) {
        item { PageTitle("履歴") }
        if (calls == null) {
            item {
                Text(
                    "読み込み中…",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(24.dp),
                )
            }
        }
        items(groups, key = { it.first.toString() }) { (day, list) ->
            SectionLabel(formatDay(day))
            Card(Modifier.padding(horizontal = 16.dp)) {
                list.forEachIndexed { i, c ->
                    if (i > 0) RowDivider()
                    CallRow(c, clock = true) { onOpenCall(c.id) }
                }
            }
        }
    }
}
