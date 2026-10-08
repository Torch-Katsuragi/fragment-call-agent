package jp.sleeptree.fragment.ui

import android.app.TimePickerDialog
import android.widget.Toast
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import jp.sleeptree.fragment.Prefs
import jp.sleeptree.fragment.api.FragmentApi
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

val HOUR_DAYS = listOf("mon" to "月", "tue" to "火", "wed" to "水", "thu" to "木", "fri" to "金", "sat" to "土", "sun" to "日", "hol" to "祝")

/** 受付時間を一行で。「いつでも」「月〜金 8:00〜17:00」 (管制室の hoursText と同じ形) */
fun hoursText(days: Map<String, List<FragmentApi.Block>>?): String {
    if (days == null) return "読み込み中…"
    val groups = mutableListOf<Pair<MutableList<String>, String>>()
    for ((k, label) in HOUR_DAYS) {
        val b = days[k].orEmpty()
        if (b.isEmpty()) continue
        val text = b.joinToString(", ") { "${it.start}〜${it.end}" }
        val last = groups.lastOrNull()
        if (last != null && last.second == text) last.first.add(label) else groups.add(mutableListOf(label) to text)
    }
    if (groups.isEmpty()) return "いつでも"
    return groups.joinToString(" / ") { (d, t) ->
        (if (d.size > 2) "${d.first()}〜${d.last()}" else d.joinToString("・")) + " " + t
    }
}

/**
 * 自分の受付時間の編集 (2026-09-29)。この時間だけ自分の端末が着信と取り次ぎで鳴る。
 * 曜日ごとにオン/オフと 1 つの時間帯。⚠2 つ目以降の時間帯 (管制室で足したもの) は触らずに残す。
 * 終わりが始まり以前なら日をまたぐ (22:00〜6:00)。
 */
@Composable
fun HoursEditor(prefs: Prefs, initial: Map<String, List<FragmentApi.Block>>, onSaved: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val days = remember { mutableStateMapOf<String, List<FragmentApi.Block>>().apply { putAll(initial) } }
    var busy by remember { mutableStateOf(false) }

    fun pick(k: String, which: Int) {
        val cur = days[k]?.firstOrNull() ?: FragmentApi.Block("08:00", "17:00")
        val v = if (which == 0) cur.start else cur.end
        val (h, m) = v.split(":").map { it.toIntOrNull() ?: 0 }
        TimePickerDialog(ctx, { _, hh, mm ->
            val s = "%02d:%02d".format(hh, mm)
            val nb = if (which == 0) cur.copy(start = s) else cur.copy(end = s)
            days[k] = listOf(nb) + days[k].orEmpty().drop(1)
        }, h, m, true).show()
    }

    Column {
        HOUR_DAYS.forEachIndexed { i, (k, label) ->
            if (i > 0) RowDivider()
            val b = days[k]?.firstOrNull()
            Row(
                Modifier.fillMaxWidth().padding(horizontal = 18.dp, vertical = 6.dp),
                verticalAlignment = androidx.compose.ui.Alignment.CenterVertically,
            ) {
                Text(
                    label,
                    style = MaterialTheme.typography.bodyLarge,
                    color = if (k in setOf("sat", "sun", "hol")) MaterialTheme.colorScheme.error
                    else MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier.padding(end = 12.dp),
                )
                Row(Modifier.weight(1f), horizontalArrangement = Arrangement.Start) {
                    if (b != null) {
                        TextButton(onClick = { pick(k, 0) }) { Text(b.start) }
                        Text("〜", modifier = Modifier.align(androidx.compose.ui.Alignment.CenterVertically))
                        TextButton(onClick = { pick(k, 1) }) { Text(b.end) }
                        if (days[k].orEmpty().size > 1) {
                            Text(
                                "ほか${days[k]!!.size - 1}",
                                style = MaterialTheme.typography.bodySmall,
                                modifier = Modifier.align(androidx.compose.ui.Alignment.CenterVertically),
                            )
                        }
                    } else {
                        Text(
                            "鳴らさない",
                            style = MaterialTheme.typography.bodyMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(vertical = 12.dp),
                        )
                    }
                }
                Switch(
                    checked = b != null,
                    onCheckedChange = { on ->
                        if (on) {
                            // 直前の曜日の時間帯を写す (平日が同じなのが普通なので)
                            val prev = HOUR_DAYS.take(i).reversed().firstNotNullOfOrNull { days[it.first]?.firstOrNull() }
                            days[k] = listOf(prev ?: FragmentApi.Block("08:00", "17:00"))
                        } else days.remove(k)
                    },
                    colors = SwitchDefaults.colors(checkedTrackColor = MaterialTheme.colorScheme.primary),
                )
            }
        }
        RowDivider()
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), horizontalArrangement = Arrangement.End) {
            TextButton(enabled = !busy, onClick = {
                days.clear()
            }) { Text("いつでも鳴らす") }
            TextButton(enabled = !busy, onClick = {
                busy = true
                scope.launch {
                    val err = withContext(Dispatchers.IO) { FragmentApi(prefs).setMyHours(days.toMap()) }
                    busy = false
                    if (err == null) {
                        Toast.makeText(ctx, "保存しました", Toast.LENGTH_SHORT).show()
                        onSaved()
                    } else Toast.makeText(ctx, err, Toast.LENGTH_LONG).show()
                }
            }) { Text("保存") }
        }
    }
}
