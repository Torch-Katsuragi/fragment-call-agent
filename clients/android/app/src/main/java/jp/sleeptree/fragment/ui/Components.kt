package jp.sleeptree.fragment.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.rounded.KeyboardArrowRight
import androidx.compose.material.icons.rounded.CallMade
import androidx.compose.material.icons.rounded.CallReceived
import androidx.compose.material.icons.rounded.Person
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import jp.sleeptree.fragment.api.FragmentApi
import kotlinx.coroutines.delay
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter

// 画面をまたいで使う部品。形の決まりは Theme.kt の注記

val CardShape = RoundedCornerShape(20.dp)

/** 白い面。中身を並べる器 */
@Composable
fun Card(
    modifier: Modifier = Modifier,
    color: Color = MaterialTheme.colorScheme.surfaceContainerLowest,
    onClick: (() -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    if (onClick != null) {
        Surface(onClick = onClick, modifier = modifier.fillMaxWidth(), shape = CardShape, color = color) {
            Column(content = content)
        }
    } else {
        Surface(modifier = modifier.fillMaxWidth(), shape = CardShape, color = color) {
            Column(content = content)
        }
    }
}

/** ページの見出し (大きく、左寄せ) */
@Composable
fun PageTitle(text: String, modifier: Modifier = Modifier, trailing: @Composable (() -> Unit)? = null) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(start = 20.dp, end = 12.dp, top = 20.dp, bottom = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(text, style = MaterialTheme.typography.headlineMedium, modifier = Modifier.weight(1f))
        trailing?.invoke()
    }
}

/** 節の見出し (小さく) */
@Composable
fun SectionLabel(text: String, modifier: Modifier = Modifier, trailing: @Composable (() -> Unit)? = null) {
    Row(
        modifier = modifier
            .fillMaxWidth()
            .padding(start = 24.dp, end = 12.dp, top = 20.dp, bottom = 8.dp)
            .heightIn(min = 24.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text,
            style = MaterialTheme.typography.labelLarge,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.weight(1f),
        )
        trailing?.invoke()
    }
}

/** 設定の1行: 名前 + 一行の説明 + 右端の操作 */
@Composable
fun SettingRow(
    title: String,
    desc: String? = null,
    onClick: (() -> Unit)? = null,
    leading: @Composable (() -> Unit)? = null,
    trailing: @Composable (() -> Unit)? = null,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .then(if (onClick != null) Modifier.clickable(onClick = onClick) else Modifier)
            .heightIn(min = 60.dp)
            .padding(horizontal = 18.dp, vertical = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (leading != null) {
            leading()
            Spacer(Modifier.width(14.dp))
        }
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            if (!desc.isNullOrEmpty()) {
                Text(
                    desc,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(top = 2.dp),
                )
            }
        }
        if (trailing != null) {
            Spacer(Modifier.width(12.dp))
            trailing()
        }
    }
}

@Composable
fun ToggleRow(title: String, desc: String?, checked: Boolean, onChange: (Boolean) -> Unit) {
    SettingRow(
        title = title,
        desc = desc,
        onClick = { onChange(!checked) },
        trailing = {
            Switch(
                checked = checked,
                onCheckedChange = onChange,
                colors = SwitchDefaults.colors(checkedTrackColor = MaterialTheme.colorScheme.primary),
            )
        },
    )
}

/** カードの中の行の区切り */
@Composable
fun RowDivider() {
    HorizontalDivider(
        modifier = Modifier.padding(start = 18.dp),
        color = MaterialTheme.colorScheme.outlineVariant,
    )
}

/** 丸いアイコン。名前があれば頭文字、無ければ人の形 */
@Composable
fun Avatar(name: String?, size: Dp = 44.dp, container: Color? = null, content: Color? = null) {
    val bg = container ?: MaterialTheme.colorScheme.secondaryContainer
    val fg = content ?: MaterialTheme.colorScheme.onSecondaryContainer
    Box(
        modifier = Modifier
            .size(size)
            .clip(CircleShape)
            .background(bg),
        contentAlignment = Alignment.Center,
    ) {
        val initial = name?.trim()?.firstOrNull()?.takeIf { !it.isDigit() && it != '+' }
        if (initial != null) {
            Text(
                initial.toString(),
                color = fg,
                style = if (size >= 64.dp) MaterialTheme.typography.headlineMedium else MaterialTheme.typography.titleMedium,
            )
        } else {
            Icon(Icons.Rounded.Person, contentDescription = null, tint = fg, modifier = Modifier.size(size * 0.55f))
        }
    }
}

/** 通話中の赤い点 */
@Composable
fun LiveDot(size: Dp = 8.dp) {
    Box(
        Modifier
            .size(size)
            .clip(CircleShape)
            .background(LocalSemantic.current.live)
    )
}

/** 経過時間 (m:ss)。1 秒ごとに描き直す */
@Composable
fun Elapsed(since: Long, style: androidx.compose.ui.text.TextStyle = MaterialTheme.typography.labelLarge, color: Color = Color.Unspecified) {
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }
    LaunchedEffect(since) {
        while (true) {
            now = System.currentTimeMillis()
            delay(1000)
        }
    }
    Text(formatElapsed(((now - since) / 1000).coerceAtLeast(0)), style = style, color = color)
}

fun formatElapsed(sec: Long): String = "%d:%02d".format(sec / 60, sec % 60)

/** 2分13秒 / 45秒 */
fun formatDuration(sec: Long): String = when {
    sec < 60 -> "${sec}秒"
    sec < 3600 -> "${sec / 60}分${sec % 60}秒"
    else -> "${sec / 3600}時間${sec % 3600 / 60}分"
}

private val zone: ZoneId get() = ZoneId.systemDefault()
private val hm = DateTimeFormatter.ofPattern("H:mm")

/** 一覧の右端の時刻。今日は時刻、昨日は「昨日」、それより前は日付 */
fun formatWhen(ms: Long): String {
    val t = Instant.ofEpochMilli(ms).atZone(zone)
    val today = LocalDate.now(zone)
    val d = t.toLocalDate()
    return when {
        d == today -> t.format(hm)
        d == today.minusDays(1) -> "昨日"
        d.year == today.year -> "${d.monthValue}/${d.dayOfMonth}"
        else -> "${d.year}/${d.monthValue}/${d.dayOfMonth}"
    }
}

private val dow = arrayOf("月", "火", "水", "木", "金", "土", "日")

/** 日付の見出し。今日 / 昨日 / 9月22日 (月) */
fun formatDay(d: LocalDate): String {
    val today = LocalDate.now(zone)
    return when (d) {
        today -> "今日"
        today.minusDays(1) -> "昨日"
        else -> "${if (d.year != today.year) "${d.year}年" else ""}${d.monthValue}月${d.dayOfMonth}日 (${dow[d.dayOfWeek.value - 1]})"
    }
}

fun localDate(ms: Long): LocalDate = Instant.ofEpochMilli(ms).atZone(zone).toLocalDate()

/** 9/24 14:32 */
fun formatDateTime(ms: Long): String {
    val t = Instant.ofEpochMilli(ms).atZone(zone)
    return "${t.monthValue}/${t.dayOfMonth} ${t.format(hm)}"
}

/**
 * 通話一覧の1行 (2026-09-26 に展開式へ)。
 *   閉じているとき … 名前 (無ければ番号)、名前があれば番号を小さく、誰が応対したか・長さ
 *   頭を押す      … 展開して要約を出す。要約は少し色を付け、押すと会話 (通話画面) へ
 *   頭をもう一度押す … 閉じる
 * ⚠通話中の行は展開しない (押せばそのまま通話画面)
 */
@Composable
fun CallRow(
    call: FragmentApi.Call,
    expanded: Boolean = false,
    onToggle: () -> Unit = {},
    onOpen: () -> Unit,
) {
    val sem = LocalSemantic.current
    Column(Modifier.fillMaxWidth()) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .clickable(onClick = if (call.active) onOpen else onToggle)
                .padding(horizontal = 18.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Avatar(call.callerLabel ?: call.name ?: call.lookupName, size = 44.dp)
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(
                        call.displayName,
                        style = MaterialTheme.typography.titleMedium,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                    Spacer(Modifier.width(8.dp))
                    if (call.active) {
                        LiveDot()
                        Spacer(Modifier.width(6.dp))
                        Text("通話中", style = MaterialTheme.typography.labelMedium, color = sem.live)
                    } else {
                        Text(
                            formatDateTime(call.startedAt).substringAfter(' '),
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                Spacer(Modifier.size(2.dp))
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    Icon(
                        if (call.outbound) Icons.Rounded.CallMade else Icons.Rounded.CallReceived,
                        contentDescription = if (call.outbound) "発信" else "着信",
                        modifier = Modifier.size(14.dp),
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(
                        callSubtitle(call),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
            }
        }
        if (expanded && !call.active) {
            // 要約。無い通話 (要約を作る前・作れなかった) は直近の発話で代える
            val body = call.summary?.takeIf { it.isNotBlank() }
                ?: call.segments.filter { it.speaker == "caller" || it.speaker == "ai" || it.speaker == "user" }
                    .joinToString("\n") { "${speakerLabel(it.speaker)}: ${it.text}" }
                    .ifEmpty { "発話はありません" }
            Row(
                Modifier
                    .fillMaxWidth()
                    .padding(start = 76.dp, end = 14.dp, bottom = 12.dp)
                    .clip(RoundedCornerShape(12.dp))
                    .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.10f))
                    .clickable(onClick = onOpen)
                    .padding(start = 12.dp, end = 6.dp, top = 10.dp, bottom = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Text(body, style = MaterialTheme.typography.bodyMedium, modifier = Modifier.weight(1f))
                Icon(
                    Icons.AutoMirrored.Rounded.KeyboardArrowRight,
                    contentDescription = "会話を開く",
                    tint = MaterialTheme.colorScheme.primary,
                )
            }
        }
    }
}

/**
 * 「山田建設 · 0312345678 · AI · 2分13秒」。
 * この通話の相手の呼び名 (callerLabel) を大きく出しているときは、電話帳の名前を小さく添える。番号は名前が分かっているときだけ
 */
private fun callSubtitle(c: FragmentApi.Call): String {
    val parts = mutableListOf<String>()
    if (c.callerLabel != null) c.registeredName?.let { parts += it }
    if ((c.hasName || c.callerLabel != null) && c.number != null) parts += c.number
    parts += if (c.outbound || c.answeredByMe) "あなた" else "AI"
    c.endedAt?.let { parts += formatDuration((it - c.startedAt) / 1000) }
    return parts.joinToString(" · ")
}
