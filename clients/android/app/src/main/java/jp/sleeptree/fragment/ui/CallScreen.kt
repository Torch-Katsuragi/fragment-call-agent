package jp.sleeptree.fragment.ui

import android.widget.Toast
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.ArrowBack
import androidx.compose.material.icons.automirrored.rounded.Send
import androidx.compose.material.icons.automirrored.rounded.VolumeUp
import androidx.compose.material.icons.rounded.Bluetooth
import androidx.compose.material.icons.rounded.Call
import androidx.compose.material.icons.rounded.CallEnd
import androidx.compose.material.icons.rounded.Explore
import androidx.compose.material.icons.rounded.Headphones
import androidx.compose.material.icons.rounded.Hearing
import androidx.compose.material.icons.rounded.Lightbulb
import androidx.compose.material.icons.rounded.Mic
import androidx.compose.material.icons.rounded.MicOff
import androidx.compose.material.icons.rounded.SmartToy
import androidx.compose.material.icons.rounded.WarningAmber
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import jp.sleeptree.fragment.Prefs
import jp.sleeptree.fragment.ServerState
import jp.sleeptree.fragment.api.FragmentApi
import jp.sleeptree.fragment.audio.CallAudio
import jp.sleeptree.fragment.call.CallCoordinator
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 通話画面。進行中なら会話・フラグメント・耳打ち・操作、終わった通話なら記録として読む。
 *
 * ⚠この画面を開いている間は**必ず音を出す** (2026-09-18 ユーザー「その画面を開いてたら確定で流す」)。
 *   閉じたら離す (応対中は離しても繋がったまま — CallAudio が operator を持っている)
 */
@Composable
fun CallScreen(prefs: Prefs, callId: String, onBack: () -> Unit, onRedial: (String) -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val api = remember(prefs) { FragmentApi(prefs) }
    var call by remember(callId) { mutableStateOf<FragmentApi.Call?>(null) }
    var failed by remember(callId) { mutableStateOf(false) }
    // 送ったが DB にまだ現れていない耳打ち
    val pendingWhispers = remember(callId) { mutableStateListOf<String>() }

    LaunchedEffect(callId) {
        while (true) {
            val c = withContext(Dispatchers.IO) { api.call(callId) }
            if (c != null) {
                call = c
                failed = false
                pendingWhispers.removeAll { t -> c.segments.any { it.speaker == "whisper" && it.text == t } }
                if (!c.active) break
            } else if (call == null) {
                failed = true
            }
            delay(1500)
        }
    }

    val c = call
    val active = c?.active == true
    DisposableEffect(c?.room, active) {
        val key = "call:$callId"
        if (c != null && active) CallAudio.hold(ctx, c.room, key, audible = true)
        onDispose { CallAudio.release(key) }
    }
    val audio by CallAudio.state.collectAsState()
    val interim by CallAudio.interim.collectAsState()
    val levels by CallAudio.levels.collectAsState()
    val mine = c != null && audio.room == c.room
    val operator = mine && audio.operator

    var confirmHangup by remember { mutableStateOf(false) }

    Column(
        Modifier
            .fillMaxSize()
            .systemBarsPadding()
            .imePadding()
    ) {
        TopBar(c, active, onBack, onRedial)

        if (c == null) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text(
                    if (failed) "通話を読み込めませんでした" else "読み込み中…",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            return@Column
        }

        if (active) {
            StatusStrip(operator, audio, levels.me)
        }
        if (c.fragments.isNotEmpty()) {
            FragmentsRow(c.fragments)
        }
        Transcript(
            segments = c.segments,
            pendingWhispers = pendingWhispers,
            interim = if (mine) interim else emptyList(),
            modifier = Modifier.weight(1f),
        )
        if (active) {
            WhisperBar { text ->
                pendingWhispers += text
                scope.launch {
                    val ok = withContext(Dispatchers.IO) { api.whisper(callId, text) }
                    if (!ok) {
                        pendingWhispers.remove(text)
                        Toast.makeText(ctx, "耳打ちを送れませんでした", Toast.LENGTH_SHORT).show()
                    }
                }
            }
            Controls(
                operator = operator,
                audio = audio,
                onToggleOperator = { CallAudio.setOperator(ctx, c.room, !operator) },
                onHangup = { confirmHangup = true },
            )
        } else if (c.number != null) {
            Row(Modifier.fillMaxWidth().padding(16.dp), horizontalArrangement = Arrangement.Center) {
                FilledTonalButton(onClick = { onRedial(c.number) }) {
                    Icon(Icons.Rounded.Call, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(8.dp))
                    Text("かけ直す")
                }
            }
        }
    }

    if (confirmHangup) {
        AlertDialog(
            onDismissRequest = { confirmHangup = false },
            title = { Text("通話を切りますか？") },
            text = { Text("相手との回線ごと切れます。") },
            confirmButton = {
                TextButton(onClick = {
                    confirmHangup = false
                    // 本人の Connection (OS の通話) も一緒に畳む。サーバーの通話を切るのは CallCoordinator
                    if (ServerState.activeCallId.value == callId) {
                        CallCoordinator.hangup(ctx)
                    } else {
                        scope.launch(Dispatchers.IO) { api.hangupCall(callId) }
                        CallAudio.endAll()
                    }
                }) { Text("切る", color = LocalSemantic.current.danger) }
            },
            dismissButton = { TextButton(onClick = { confirmHangup = false }) { Text("やめる") } },
        )
    }
}

@Composable
private fun TopBar(c: FragmentApi.Call?, active: Boolean, onBack: () -> Unit, onRedial: (String) -> Unit) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(start = 4.dp, end = 12.dp, top = 4.dp, bottom = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        IconButton(onClick = onBack) {
            Icon(Icons.AutoMirrored.Rounded.ArrowBack, contentDescription = "戻る")
        }
        if (c == null) return@Row
        Avatar(c.name, size = 40.dp)
        Spacer(Modifier.width(12.dp))
        Column(Modifier.weight(1f)) {
            Text(
                c.displayName,
                style = MaterialTheme.typography.titleMedium,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                val sub = MaterialTheme.typography.bodySmall
                val subColor = MaterialTheme.colorScheme.onSurfaceVariant
                if (active) {
                    LiveDot(6.dp)
                    Spacer(Modifier.width(6.dp))
                    Text("通話中 ", style = sub, color = subColor)
                    Elapsed(c.startedAt, style = sub, color = subColor)
                } else {
                    val dur = c.endedAt?.let { " · " + formatDuration((it - c.startedAt) / 1000) }.orEmpty()
                    Text(
                        (if (c.outbound) "発信 · " else "") + formatDateTime(c.startedAt) + dur,
                        style = sub,
                        color = subColor,
                    )
                }
                if (c.name != null && c.number != null) {
                    Text(" · ${c.number}", style = sub, color = subColor, maxLines = 1)
                }
            }
        }
    }
}

/** 誰が話しているか・音の出どころ */
@Composable
private fun StatusStrip(operator: Boolean, audio: CallAudio.State, myLevel: Float) {
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 16.dp, vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        HandlerPill(operator)
        if (operator) {
            Spacer(Modifier.width(8.dp))
            // 自分の声を拾えていれば光る (マイクの確認)
            Box(
                Modifier
                    .size(8.dp)
                    .clip(CircleShape)
                    .background(if (myLevel > 0.04f && !audio.muted) LocalSemantic.current.me else MaterialTheme.colorScheme.outlineVariant)
            )
        }
        Spacer(Modifier.weight(1f))
        val note = when {
            audio.error != null -> audio.error
            !audio.connected -> "音声に接続中…"
            audio.muted -> "ミュート中"
            else -> null
        }
        if (note != null) {
            Text(note, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

/** フラグメント (会話監視エージェントの気づき)。横に並べ、押したら中身を開く */
@Composable
private fun FragmentsRow(items: List<FragmentApi.Fragment>) {
    var open by remember { mutableStateOf<Long?>(null) }
    LazyRow(
        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        items(items, key = { it.id }) { f ->
            val (bg, fg) = fragmentColors(f.kind)
            Surface(
                onClick = { open = if (open == f.id) null else f.id },
                shape = RoundedCornerShape(14.dp),
                color = bg,
                border = if (open == f.id) BorderStroke(1.5.dp, fg.copy(alpha = 0.5f)) else null,
            ) {
                Row(Modifier.padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    Icon(fragmentIcon(f.kind), contentDescription = null, tint = fg, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(6.dp))
                    Text(
                        f.title.ifBlank { f.text.take(18) },
                        style = MaterialTheme.typography.labelLarge,
                        color = fg,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.widthIn(max = 220.dp),
                    )
                }
            }
        }
    }
    val f = items.firstOrNull { it.id == open }
    if (f != null) {
        val (bg, fg) = fragmentColors(f.kind)
        Surface(
            onClick = { open = null },
            color = bg,
            shape = RoundedCornerShape(16.dp),
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 4.dp),
        ) {
            Column(Modifier.padding(horizontal = 16.dp, vertical = 12.dp)) {
                if (f.title.isNotBlank()) Text(f.title, style = MaterialTheme.typography.titleSmall, color = fg)
                Text(
                    f.text,
                    style = MaterialTheme.typography.bodyMedium,
                    color = fg,
                    modifier = Modifier
                        .padding(top = 4.dp)
                        .heightIn(max = 180.dp),
                )
            }
        }
    }
}

@Composable
private fun fragmentColors(kind: String): Pair<Color, Color> {
    val s = MaterialTheme.colorScheme
    return when (kind) {
        "alert" -> s.errorContainer to s.onErrorContainer
        "hint" -> s.tertiaryContainer to s.onTertiaryContainer
        else -> s.secondaryContainer to s.onSecondaryContainer
    }
}

private fun fragmentIcon(kind: String): ImageVector = when (kind) {
    "alert" -> Icons.Rounded.WarningAmber
    "hint" -> Icons.Rounded.Explore
    else -> Icons.Rounded.Lightbulb
}

private data class Bubble(val key: String, val speaker: String, val text: String, val pending: Boolean)

/** 会話。相手は左、こちら側 (AI・あなた・耳打ち) は右 */
@Composable
private fun Transcript(
    segments: List<FragmentApi.Segment>,
    pendingWhispers: List<String>,
    interim: List<CallAudio.Interim>,
    modifier: Modifier = Modifier,
) {
    // ⚠確定済みの発話と同じ文の途中表示は出さない (DB に入った後も数秒残るため)
    val done = segments.takeLast(6).map { it.text.trim() }.toSet()
    val bubbles = segments.mapIndexed { i, s -> Bubble("s$i", s.speaker, s.text, false) } +
        pendingWhispers.mapIndexed { i, t -> Bubble("w$i", "whisper", t, true) } +
        interim.filter { it.text.isNotBlank() && it.text.trim() !in done }
            .map { Bubble("i${it.key}", it.speaker, it.text, true) }

    val listState = rememberLazyListState()
    val last = bubbles.lastOrNull()
    LaunchedEffect(bubbles.size, last?.text) {
        if (bubbles.isNotEmpty()) listState.scrollToItem(bubbles.size - 1)
    }
    if (bubbles.isEmpty()) {
        Box(modifier.fillMaxWidth(), contentAlignment = Alignment.Center) {
            Text("まだ発話がありません", color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        return
    }
    LazyColumn(
        modifier = modifier.fillMaxWidth(),
        state = listState,
        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 10.dp),
    ) {
        items(bubbles.size, key = { bubbles[it].key }) { i ->
            val b = bubbles[i]
            val first = i == 0 || bubbles[i - 1].speaker != b.speaker
            MessageBubble(b, first)
        }
    }
}

@Composable
private fun MessageBubble(b: Bubble, firstOfRun: Boolean) {
    val sem = LocalSemantic.current
    val left = b.speaker == "caller"
    val whisper = b.speaker == "whisper"
    val (bg, fg) = when (b.speaker) {
        "caller" -> sem.callerBubble to MaterialTheme.colorScheme.onSurface
        "user" -> sem.meBubble to sem.onMeBubble
        "whisper" -> Color.Transparent to MaterialTheme.colorScheme.onSurface
        else -> sem.aiBubble to MaterialTheme.colorScheme.onSurface
    }
    val r = 18.dp
    val tail = 6.dp
    val shape = when {
        !firstOfRun -> RoundedCornerShape(r)
        left -> RoundedCornerShape(topStart = tail, topEnd = r, bottomEnd = r, bottomStart = r)
        else -> RoundedCornerShape(topStart = r, topEnd = tail, bottomEnd = r, bottomStart = r)
    }
    Column(
        Modifier
            .fillMaxWidth()
            .padding(top = if (firstOfRun) 10.dp else 3.dp),
        horizontalAlignment = if (left) Alignment.Start else Alignment.End,
    ) {
        if (firstOfRun) {
            Text(
                when (b.speaker) {
                    "caller" -> "相手"
                    "user" -> "あなた"
                    "whisper" -> "耳打ち · 相手には聞こえません"
                    else -> "AI"
                },
                style = MaterialTheme.typography.labelSmall,
                color = if (b.speaker == "caller") sem.caller else MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(horizontal = 6.dp, vertical = 3.dp),
            )
        }
        Box(
            Modifier
                .widthIn(max = 300.dp)
                .alpha(if (b.pending) 0.6f else 1f)
                .clip(shape)
                .background(bg)
                .then(
                    when {
                        whisper -> Modifier.border(1.dp, MaterialTheme.colorScheme.primary.copy(alpha = 0.5f), shape)
                        left -> Modifier.border(1.dp, MaterialTheme.colorScheme.outlineVariant, shape)
                        else -> Modifier
                    }
                )
                .padding(horizontal = 14.dp, vertical = 9.dp)
        ) {
            Text(b.text, style = MaterialTheme.typography.bodyLarge, color = fg)
        }
    }
}

/** AIへの耳打ち */
@Composable
private fun WhisperBar(onSend: (String) -> Unit) {
    var text by remember { mutableStateOf("") }
    val send = {
        val t = text.trim()
        if (t.isNotEmpty()) {
            onSend(t)
            text = ""
        }
    }
    Row(
        Modifier
            .fillMaxWidth()
            .padding(start = 12.dp, end = 6.dp, top = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        TextField(
            value = text,
            onValueChange = { if (it.length <= 500) text = it },
            placeholder = { Text("AIへ耳打ち (相手には聞こえません)") },
            modifier = Modifier.weight(1f),
            shape = RoundedCornerShape(24.dp),
            maxLines = 3,
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
            keyboardActions = KeyboardActions(onSend = { send() }),
            colors = TextFieldDefaults.colors(
                focusedIndicatorColor = Color.Transparent,
                unfocusedIndicatorColor = Color.Transparent,
                disabledIndicatorColor = Color.Transparent,
                focusedContainerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
                unfocusedContainerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
            ),
        )
        IconButton(onClick = send, enabled = text.isNotBlank()) {
            Icon(
                Icons.AutoMirrored.Rounded.Send,
                contentDescription = "送る",
                tint = if (text.isNotBlank()) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.outline,
            )
        }
    }
}

/** 下の操作列: ミュート / 出力先 / 自分が出る⇔AIに任せる / 終了 */
@Composable
private fun Controls(
    operator: Boolean,
    audio: CallAudio.State,
    onToggleOperator: () -> Unit,
    onHangup: () -> Unit,
) {
    val sem = LocalSemantic.current
    val s = MaterialTheme.colorScheme
    var routeMenu by remember { mutableStateOf(false) }
    Row(
        Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 14.dp),
        horizontalArrangement = Arrangement.SpaceEvenly,
    ) {
        ControlButton(
            label = if (audio.muted) "ミュート中" else "ミュート",
            icon = if (audio.muted) Icons.Rounded.MicOff else Icons.Rounded.Mic,
            container = if (audio.muted) s.onSurface else s.surfaceContainerHigh,
            content = if (audio.muted) s.surface else s.onSurface,
            enabled = operator && audio.connected,
            onClick = { CallAudio.setMuted(!audio.muted) },
        )
        Box {
            val route = audio.route
            ControlButton(
                label = routeLabel(route),
                icon = routeIcon(route),
                container = if (route == CallAudio.Route.SPEAKER) s.onSurface else s.surfaceContainerHigh,
                content = if (route == CallAudio.Route.SPEAKER) s.surface else s.onSurface,
                enabled = audio.connected && audio.routes.size > 1,
                onClick = {
                    // 受話口とスピーカーだけなら切り替え。イヤホン・Bluetooth があれば選ぶ
                    val others = audio.routes.filter { it != CallAudio.Route.EARPIECE && it != CallAudio.Route.SPEAKER }
                    if (others.isEmpty()) {
                        CallAudio.selectRoute(
                            if (route == CallAudio.Route.SPEAKER) CallAudio.Route.EARPIECE else CallAudio.Route.SPEAKER
                        )
                    } else {
                        routeMenu = true
                    }
                },
            )
            DropdownMenu(expanded = routeMenu, onDismissRequest = { routeMenu = false }) {
                audio.routes.forEach { r ->
                    DropdownMenuItem(
                        text = { Text(routeLabel(r)) },
                        leadingIcon = { Icon(routeIcon(r), contentDescription = null) },
                        onClick = {
                            routeMenu = false
                            CallAudio.selectRoute(r)
                        },
                    )
                }
            }
        }
        ControlButton(
            label = if (operator) "AIに任せる" else "自分が出る",
            icon = if (operator) Icons.Rounded.SmartToy else Icons.Rounded.Mic,
            container = if (operator) s.surfaceContainerHigh else sem.me,
            content = if (operator) s.onSurface else Color.White,
            onClick = onToggleOperator,
        )
        ControlButton(
            label = "終了",
            icon = Icons.Rounded.CallEnd,
            container = sem.danger,
            content = Color.White,
            onClick = onHangup,
        )
    }
}

private fun routeLabel(r: CallAudio.Route?) = when (r) {
    CallAudio.Route.SPEAKER -> "スピーカー"
    CallAudio.Route.EARPIECE -> "受話口"
    CallAudio.Route.BLUETOOTH -> "Bluetooth"
    CallAudio.Route.WIRED -> "イヤホン"
    null -> "音声"
}

private fun routeIcon(r: CallAudio.Route?): ImageVector = when (r) {
    CallAudio.Route.EARPIECE -> Icons.Rounded.Hearing
    CallAudio.Route.BLUETOOTH -> Icons.Rounded.Bluetooth
    CallAudio.Route.WIRED -> Icons.Rounded.Headphones
    else -> Icons.AutoMirrored.Rounded.VolumeUp
}

@Composable
private fun ControlButton(
    label: String,
    icon: ImageVector,
    container: Color,
    content: Color,
    enabled: Boolean = true,
    onClick: () -> Unit,
) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier
            .width(76.dp)
            .alpha(if (enabled) 1f else 0.38f),
    ) {
        Surface(
            onClick = onClick,
            enabled = enabled,
            shape = CircleShape,
            color = container,
            modifier = Modifier.size(60.dp),
        ) {
            Box(contentAlignment = Alignment.Center) {
                Icon(icon, contentDescription = label, tint = content, modifier = Modifier.size(26.dp))
            }
        }
        Spacer(Modifier.height(6.dp))
        Text(
            label,
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            maxLines = 1,
        )
    }
}

/**
 * 応答したが、通話の id がまだ分からない間の画面。
 * ⚠着信 (CALL) に出たときはルームすら無い — 受話 → ダイヤルプランがブリッジ → agent が通話行を作る、
 *   まで数秒かかる。**どの通話でもよいから**通話が現れたらそれに operator で入る (2026-09-19 の注記)
 */
@Composable
fun JoiningScreen(prefs: Prefs, room: String?, onJoined: (String) -> Unit, onGiveUp: () -> Unit) {
    val ctx = LocalContext.current
    var gaveUp by remember { mutableStateOf(false) }
    LaunchedEffect(room) {
        val api = FragmentApi(prefs)
        repeat(40) {
            val a = withContext(Dispatchers.IO) { api.deviceState()?.activeCall }
            if (a != null && (room == null || a.room == room)) {
                CallAudio.setOperator(ctx, a.room, true)
                onJoined(a.id)
                return@LaunchedEffect
            }
            delay(700)
        }
        gaveUp = true
    }
    Column(
        Modifier
            .fillMaxSize()
            .systemBarsPadding()
            .padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            if (gaveUp) "通話が見つかりませんでした" else "つないでいます…",
            style = MaterialTheme.typography.titleMedium,
        )
        if (gaveUp) {
            Spacer(Modifier.height(16.dp))
            TextButton(onClick = onGiveUp) { Text("閉じる") }
        }
    }
}
