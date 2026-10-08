package jp.sleeptree.fragment.ui

import kotlinx.coroutines.withContext
import kotlinx.coroutines.launch
import kotlinx.coroutines.Dispatchers
import androidx.compose.ui.platform.LocalContext
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.material3.TextButton
import androidx.compose.material3.IconButton
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.AlertDialog
import androidx.compose.material.icons.rounded.PersonAdd
import androidx.compose.material.icons.rounded.MoreVert
import androidx.compose.foundation.layout.Box
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.compose.rememberLauncherForActivityResult
import android.widget.Toast
import android.provider.ContactsContract
import android.content.Intent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.Call
import androidx.compose.material.icons.rounded.History
import androidx.compose.material.icons.rounded.Search
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import jp.sleeptree.fragment.Prefs
import jp.sleeptree.fragment.api.FragmentApi

/**
 * 電話帳 (2026-09-26、履歴タブの代わり)。フラグメントのサーバーの 連絡先/<番号>.md を並べる —
 * 着信のときに AI が読む「相手ごとのメモ」と同じもの。スマホの電話帳からの登録は右上のメニューから。
 *   行を押す … 展開してメモ (相手ごとのメモ) と「かける」「最後の通話」
 *   もう一度押す … 閉じる
 */
@Composable
fun ContactsScreen(prefs: Prefs, onOpenCall: (String) -> Unit, onDial: (String) -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    val api = remember(prefs) { FragmentApi(prefs) }
    // reload を進めると取り直す (登録した直後に一覧へ出すため)
    var reload by remember { mutableIntStateOf(0) }
    val contacts by rememberPolled(reload, 30_000) { api.phonebook() }
    var menu by remember { mutableStateOf(false) }

    // スマホの電話帳から 1 件選んで登録する (2026-09-26)。
    // ⚠連絡先の読み取り権限は取らない。選択画面 (ACTION_PICK) で選ばれた 1 件だけを読む
    //   (選んだ結果の URI にだけ一時的な読み取りの許可が付く)
    // (名前, 読みがな, 番号)。読みがなは連絡先のふりがな (無ければ空 = サーバーの worker が補う)
    var picked by remember { mutableStateOf<Triple<String, String, String>?>(null) }
    val pickContact = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { r ->
        val uri = r.data?.data ?: return@rememberLauncherForActivityResult
        val cols = arrayOf(
            ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME,
            ContactsContract.CommonDataKinds.Phone.NUMBER,
            ContactsContract.Contacts.PHONETIC_NAME,
        )
        runCatching {
            ctx.contentResolver.query(uri, cols, null, null, null)?.use { c ->
                if (c.moveToFirst()) picked = Triple(c.getString(0) ?: "", c.getString(2) ?: "", c.getString(1) ?: "")
            }
        }.onFailure { Toast.makeText(ctx, "連絡先を読めませんでした", Toast.LENGTH_SHORT).show() }
    }
    var query by remember { mutableStateOf("") }
    val expanded = remember { mutableStateListOf<String>() }

    val q = query.trim()
    val list = contacts.orEmpty().filter {
        q.isEmpty() || (it.name?.contains(q, ignoreCase = true) == true) || (it.kana?.contains(q) == true) || it.number.contains(q.filter(Char::isDigit).ifEmpty { q })
    }

    LazyColumn(Modifier.fillMaxWidth(), contentPadding = PaddingValues(bottom = 24.dp)) {
        item {
            PageTitle("電話帳") {
                Box {
                    IconButton(onClick = { menu = true }) {
                        Icon(Icons.Rounded.MoreVert, contentDescription = "メニュー")
                    }
                    DropdownMenu(expanded = menu, onDismissRequest = { menu = false }) {
                        DropdownMenuItem(
                            text = { Text("スマホの電話帳から追加") },
                            leadingIcon = { Icon(Icons.Rounded.PersonAdd, contentDescription = null) },
                            onClick = {
                                menu = false
                                pickContact.launch(
                                    Intent(Intent.ACTION_PICK).setType(ContactsContract.CommonDataKinds.Phone.CONTENT_TYPE)
                                )
                            },
                        )
                    }
                }
            }
        }
        item {
            TextField(
                value = query,
                onValueChange = { query = it },
                placeholder = { Text("名前・番号で探す") },
                leadingIcon = { Icon(Icons.Rounded.Search, contentDescription = null) },
                singleLine = true,
                shape = RoundedCornerShape(24.dp),
                colors = TextFieldDefaults.colors(
                    focusedIndicatorColor = Color.Transparent,
                    unfocusedIndicatorColor = Color.Transparent,
                    focusedContainerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
                    unfocusedContainerColor = MaterialTheme.colorScheme.surfaceContainerHigh,
                ),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp, vertical = 4.dp),
            )
        }
        if (contacts == null || list.isEmpty()) {
            item {
                Text(
                    when {
                        contacts == null -> "読み込み中…"
                        q.isNotEmpty() -> "見つかりません"
                        else -> "まだ登録がありません。右上のメニューからスマホの電話帳の相手を登録できます"
                    },
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(24.dp),
                )
            }
        }
        itemsIndexed(list, key = { _, c -> c.number }) { i, c ->
            val top = if (i == 0) 20.dp else 0.dp
            val bottom = if (i == list.lastIndex) 20.dp else 0.dp
            Surface(
                color = MaterialTheme.colorScheme.surfaceContainerLowest,
                shape = RoundedCornerShape(top, top, bottom, bottom),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 16.dp)
                    .padding(top = if (i == 0) 8.dp else 0.dp),
            ) {
                Column {
                    if (i > 0) RowDivider()
                    ContactRow(
                        c,
                        expanded = c.number in expanded,
                        onToggle = { if (c.number in expanded) expanded.remove(c.number) else expanded.add(c.number) },
                        onDial = { onDial(c.number) },
                        onOpenCall = onOpenCall,
                        onAnswer = { a ->
                            scope.launch {
                                val ok = withContext(Dispatchers.IO) { api.setContactAnswer(c.number, a) }
                                if (!ok) Toast.makeText(ctx, "変えられませんでした (管理以上の権限が要ります)", Toast.LENGTH_SHORT).show()
                                reload++
                            }
                        },
                    )
                }
            }
        }
    }

    picked?.let { (name, kana, number) ->
        AlertDialog(
            onDismissRequest = { picked = null },
            title = { Text("電話帳に登録") },
            text = { Text("$name${if (kana.isNotEmpty()) " ($kana)" else ""}\n$number\n\nフラグメントの電話帳に登録します。すでにある番号なら名前だけ書き換えます (メモはそのまま)。") },
            confirmButton = {
                TextButton(onClick = {
                    picked = null
                    scope.launch {
                        val n = withContext(Dispatchers.IO) { api.addContact(name, kana, listOf(number)) }
                        Toast.makeText(
                            ctx,
                            if (n != null && n > 0) "${name}を登録しました" else "登録できませんでした",
                            Toast.LENGTH_SHORT,
                        ).show()
                        reload++
                    }
                }) { Text("登録") }
            },
            dismissButton = { TextButton(onClick = { picked = null }) { Text("やめる") } },
        )
    }
}

@Composable
private fun ContactRow(
    c: FragmentApi.Contact,
    expanded: Boolean,
    onToggle: () -> Unit,
    onDial: () -> Unit,
    onOpenCall: (String) -> Unit,
    onAnswer: (String?) -> Unit,
) {
    Column(Modifier.fillMaxWidth()) {
        Row(
            Modifier
                .fillMaxWidth()
                .clickable(onClick = onToggle)
                .padding(horizontal = 18.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Avatar(c.name, size = 44.dp)
            Spacer(Modifier.width(14.dp))
            Column(Modifier.weight(1f)) {
                Text(
                    c.name ?: c.number,
                    style = MaterialTheme.typography.titleMedium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                )
                val sub = listOfNotNull(
                    c.answer,
                    c.number.takeIf { c.name != null },
                    if (c.callCount > 0) "通話 ${c.callCount} 回" else null,
                    c.lastCallAt?.let { formatWhen(it) },
                ).joinToString(" · ")
                if (sub.isNotEmpty()) {
                    Text(
                        sub,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                    )
                }
            }
        }
        if (expanded) {
            Column(Modifier.padding(start = 76.dp, end = 14.dp, bottom = 12.dp)) {
                // 相手ごとのメモ (着信のとき AI が読むもの)
                Text(
                    // md の太字 (**) はそのまま見せると読みにくいので外す
                    c.memo.replace("**", "").ifEmpty { "メモはまだありません" },
                    style = MaterialTheme.typography.bodyMedium,
                    color = if (c.memo.isEmpty()) MaterialTheme.colorScheme.onSurfaceVariant else MaterialTheme.colorScheme.onSurface,
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(RoundedCornerShape(12.dp))
                        .background(MaterialTheme.colorScheme.primary.copy(alpha = 0.10f))
                        .padding(horizontal = 12.dp, vertical = 10.dp),
                )
                // 食い違い (⚠) はメモのすぐ下に目立たせる。どちらが正しいかは決めつけず、両方を見せる。
                // ⚠「話した人」の後ろに置いたら、最後の人についての記録に見えた (Fold で実物を見て直した)
                val conflicts = (c.conflicts.lines() + c.lookup.lines().filter { it.startsWith("- ⚠") })
                    .filter { it.isNotBlank() }
                if (conflicts.isNotEmpty()) {
                    Spacer(Modifier.size(6.dp))
                    Column(
                        Modifier
                            .fillMaxWidth()
                            .clip(RoundedCornerShape(12.dp))
                            .background(MaterialTheme.colorScheme.errorContainer.copy(alpha = 0.35f))
                            .padding(horizontal = 12.dp, vertical = 8.dp)
                    ) {
                        conflicts.forEach { line ->
                            Text(
                                line.removePrefix("- "),
                                style = MaterialTheme.typography.bodySmall,
                                color = MaterialTheme.colorScheme.error,
                                modifier = Modifier.padding(vertical = 1.dp),
                            )
                        }
                    }
                }
                if (c.people.isNotEmpty()) {
                    // この番号からかけてきた人 (組織の番号は何人もが使う)
                    Spacer(Modifier.size(6.dp))
                    Text(
                        "話した人",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 4.dp),
                    )
                    c.people.lines().filter { it.isNotBlank() }.forEach { line ->
                        val head = line.startsWith("### ")
                        Text(
                            if (head) line.removePrefix("### ") else line.removePrefix("- "),
                            style = if (head) MaterialTheme.typography.labelLarge else MaterialTheme.typography.bodySmall,
                            color = if (head) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(start = if (head) 4.dp else 12.dp, end = 4.dp, top = if (head) 4.dp else 1.dp),
                        )
                    }
                }
                if (c.unsorted.isNotEmpty()) {
                    // 同じ人か迷って振り分けなかった名乗り。md の「未整理」を人が仕分ける
                    Spacer(Modifier.size(6.dp))
                    Text(
                        "未整理",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.tertiary,
                        modifier = Modifier.padding(horizontal = 4.dp),
                    )
                    c.unsorted.lines().filter { it.isNotBlank() }.forEach { line ->
                        Text(
                            line.removePrefix("- "),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(horizontal = 4.dp, vertical = 1.dp),
                        )
                    }
                }
                if (c.lookup.isNotEmpty()) {
                    // 番号検索で分かったこと。通話と食い違った記録 (⚠) は目立たせる
                    Spacer(Modifier.size(6.dp))
                    Text(
                        "番号検索",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(horizontal = 4.dp),
                    )
                    // ⚠ の行 (前の版で番号検索の節に書いていた食い違い) は上にまとめて出している
                    c.lookup.lines().filter { it.isNotBlank() && !it.startsWith("- ⚠") }.forEach { line ->
                        Text(
                            line.removePrefix("- "),
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.padding(horizontal = 4.dp, vertical = 1.dp),
                        )
                    }
                }
                // 相手ごとの応答 (2026-09-30)。回線の応答モードより優先する
                Spacer(Modifier.size(8.dp))
                Text(
                    "この相手からの電話",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 4.dp),
                )
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf(null to "設定どおり", "人が出る" to "人が出る", "AIが出る" to "AIが出る").forEach { (v, label) ->
                        androidx.compose.material3.FilterChip(
                            selected = c.answer == v,
                            onClick = { if (c.answer != v) onAnswer(v) },
                            label = { Text(label) },
                        )
                    }
                }
                Text(
                    when (c.answer) {
                        "人が出る" -> "不在のときも端末を鳴らし、出なければ AI が出ます"
                        "AIが出る" -> "いつもすぐ AI が出ます (端末は鳴りません)"
                        else -> "回線の応答モードのとおりです"
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(horizontal = 4.dp),
                )
                Spacer(Modifier.size(8.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilledTonalButton(onClick = onDial) {
                        Icon(Icons.Rounded.Call, contentDescription = null, modifier = Modifier.size(18.dp))
                        Spacer(Modifier.width(6.dp))
                        Text("かける")
                    }
                    val last = c.lastCallId
                    if (last != null) {
                        FilledTonalButton(onClick = { onOpenCall(last) }) {
                            Icon(Icons.Rounded.History, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(6.dp))
                            Text("最後の通話")
                        }
                    }
                }
            }
        }
    }
}
