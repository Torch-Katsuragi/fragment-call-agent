package jp.sleeptree.fragment.ui

import android.app.KeyguardManager
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Call
import androidx.compose.material.icons.filled.CallEnd
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.SmartToy
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import kotlinx.coroutines.delay
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import jp.sleeptree.fragment.api.FragmentApi
import jp.sleeptree.fragment.call.CallCoordinator

/**
 * 取り次ぎの着信画面。
 *
 * ⚠**ロック中と解除後で出す量を変える** (2026-08-01決定)。この画面はロック画面の上に出るので、
 *   机に置いた端末を他人が覗ける。フラグメントには【本人限定】情報 (猟銃の件・補助金事情など) が
 *   入る設計なので、**ロック中は名前と呼び出し理由だけ**にする。
 *
 * ⚠**鳴っている間も中身は更新される** — 相手はまだAIと話し続けている。
 *   WatchServiceのポーリングが CallCoordinator.incoming を差し替えるので、ここは
 *   collectAsState で受けるだけで「迷っている間に判断材料が増える」体験になる。
 */
class IncomingCallActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // 見た目の確認用 (デバッグビルドのみ)。詳細は CallCoordinator.seedForPreview
        if (jp.sleeptree.fragment.BuildConfig.DEBUG && intent?.getBooleanExtra("demo", false) == true) {
            CallCoordinator.seedForPreview()
        }
        // 通知 (バナー) の「出る」から来た。⚠前面にいるうちに応答して次の画面へ (answer の注記)
        if (intent?.getBooleanExtra(EXTRA_ANSWER, false) == true) {
            CallCoordinator.answer(this)
            finish()
            return
        }
        val keyguard = getSystemService(KeyguardManager::class.java)
        setContent {
            FragmentTheme {
                val incoming by CallCoordinator.incoming.collectAsState()
                // ⚠ロック状態は**監視しないと変わらない**。1回読むだけだと、鳴っている最中に
                //   本人が解除しても画面が「ロック解除で経緯を表示」のまま固まる (2026-08-01実測)。
                //   Keyguardに解除通知のコールバックが無いので短間隔で読み直す
                // ⚠デバッグビルドのみ: PINを入れずに解除後レイアウトを確認するための上書き
                //   (`--ez unlocked true`)。実機確認で毎回ロック解除を人に頼まないための穴
                val forceUnlocked = jp.sleeptree.fragment.BuildConfig.DEBUG &&
                    intent?.getBooleanExtra("unlocked", false) == true
                var locked by remember {
                    mutableStateOf(!forceUnlocked && (keyguard?.isDeviceLocked ?: false))
                }
                LaunchedEffect(forceUnlocked) {
                    while (true) {
                        locked = !forceUnlocked && (keyguard?.isDeviceLocked ?: false)
                        delay(400)
                    }
                }
                val inc = incoming
                if (inc == null) {
                    finish()
                } else {
                    IncomingCallScreen(
                        incoming = inc,
                        locked = locked,
                        onAnswer = { CallCoordinator.answer(this); finish() },
                        onToAi = { CallCoordinator.toAi(this); finish() },
                        onReject = { CallCoordinator.reject(this); finish() },
                    )
                }
            }
        }
    }

    override fun onResume() {
        super.onResume()
        CallCoordinator.screenVisible = true
    }

    override fun onPause() {
        super.onPause()
        CallCoordinator.screenVisible = false
    }

    companion object {
        const val EXTRA_ANSWER = "answer"
    }

    /** ⚠戻るキーで消せてはいけない。呼び出しは応答か拒否で終わる */
    @Deprecated("Deprecated in Java")
    override fun onBackPressed() {
        // 何もしない
    }
}

@Composable
private fun IncomingCallScreen(
    incoming: CallCoordinator.Incoming,
    locked: Boolean,
    onAnswer: () -> Unit,
    onToAi: () -> Unit,
    onReject: () -> Unit,
) {
    Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                // ⚠**これが無いとステータスバー(時刻・電池)とナビゲーションバーの下に潜り込む**。
                //   Android 15+ は targetSdk 35 以上で全画面描画が既定になり、
                //   インセットは自分で避ける必要がある (2026-08-01に実機で発覚)。
                //   半透明にしたかったわけではなく、単に避け損ねていた
                .safeDrawingPadding()
                .padding(horizontal = 20.dp, vertical = 20.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            // ⚠取り次ぎと着信で意味がまるで違う。取り次ぎは「AIが応対中の通話に呼ばれている」、
            //   着信は「まだ誰も出ていない — 出なければAIが預かる」。
            //   出た後の挙動も違うので、見分けが付かないと押し間違える
            Text(
                text = if (incoming.kind == CallCoordinator.Kind.HANDOFF) {
                    "フラグメントが呼んでいます"
                } else {
                    "着信 · 出なければAIが預かります"
                },
                style = MaterialTheme.typography.labelLarge,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )

            // ⚠取り次ぎには判断材料 (経緯・フラグメント・会話) が付くが、着信には何も無い。
            //   同じ配置のまま出すと**下2/3が空白の画面**になった (2026-08-01の実機で確認)。
            //   材料が無いときは普通の電話アプリと同じように相手を中央に置く
            val hasDetail = incoming.kind == CallCoordinator.Kind.HANDOFF
            if (!hasDetail) Spacer(Modifier.weight(1f))

            Spacer(Modifier.size(if (locked) 40.dp else 20.dp))

            Caller(incoming, compact = !locked && hasDetail)

            if (incoming.reason.isNotBlank()) {
                Spacer(Modifier.size(16.dp))
                Surface(
                    color = MaterialTheme.colorScheme.primaryContainer,
                    shape = RoundedCornerShape(12.dp),
                ) {
                    Text(
                        text = incoming.reason,
                        modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onPrimaryContainer,
                        textAlign = TextAlign.Center,
                    )
                }
            }

            if (!hasDetail) {
                Spacer(Modifier.weight(1f))
            } else if (locked) {
                // ⚠ここから先は他人に見せない。何かが隠れていることだけ伝える
                Spacer(Modifier.size(18.dp))
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        Icons.Filled.Lock,
                        contentDescription = null,
                        modifier = Modifier.size(14.dp),
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.size(6.dp))
                    Text(
                        "ロック解除で経緯を表示",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
                Spacer(Modifier.weight(1f))
            } else {
                Spacer(Modifier.size(20.dp))
                Column(
                    modifier = Modifier
                        .weight(1f)
                        .fillMaxWidth()
                        .verticalScroll(rememberScrollState()),
                ) {
                    if (incoming.fragments.isNotEmpty()) {
                        SectionLabel("フラグメント")
                        incoming.fragments.forEach { FragmentCard(it) }
                        Spacer(Modifier.size(14.dp))
                    }
                    if (incoming.recent.isNotEmpty()) {
                        SectionLabel("いまの会話 ・ 進行中")
                        Surface(
                            color = MaterialTheme.colorScheme.surfaceVariant,
                            shape = RoundedCornerShape(10.dp),
                            modifier = Modifier.fillMaxWidth(),
                        ) {
                            Column(Modifier.padding(10.dp)) {
                                incoming.recent.forEach { SegmentLine(it) }
                            }
                        }
                    }
                }
            }

            Spacer(Modifier.size(18.dp))

            Row(
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 24.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
            ) {
                // 3択 (2026-09-25): 切る / AIに任せる / 出る。取り次ぎは AI が既に話しているので
                // 「切る」は無く、「AIに任せる」は「AIに続けさせる」になる
                if (incoming.kind == CallCoordinator.Kind.CALL) {
                    CallButton("切る", Icons.Filled.CallEnd, Color(0xFFE24B4A), onReject)
                }
                CallButton(
                    if (incoming.kind == CallCoordinator.Kind.CALL) "AIに任せる" else "AIに続けさせる",
                    Icons.Filled.SmartToy,
                    Color(0xFF5B6B7A),
                    onToAi,
                )
                if (incoming.announcing) {
                    // 録音告知中 (2026-09-25)。出るボタンは告知が終わってから出す
                    Box(Modifier.size(72.dp), contentAlignment = Alignment.Center) {
                        Text(
                            "録音告知中…",
                            style = MaterialTheme.typography.labelMedium,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                } else {
                    CallButton("出る", Icons.Filled.Call, Color(0xFF1D9E75), onAnswer)
                }
            }
        }
    }
}

@Composable
private fun Caller(incoming: CallCoordinator.Incoming, compact: Boolean) {
    val avatar = if (compact) 48.dp else 88.dp
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Box(
            modifier = Modifier
                .size(avatar)
                .clip(CircleShape)
                .background(MaterialTheme.colorScheme.primaryContainer),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                Icons.Filled.Person,
                contentDescription = null,
                modifier = Modifier.size(avatar / 2),
                tint = MaterialTheme.colorScheme.onPrimaryContainer,
            )
        }
        Spacer(Modifier.size(12.dp))
        // ⚠name は「電話帳に載っていれば名前」。無ければ番号のweb検索の名前、それも無ければ番号
        val lookup = incoming.lookup
        val title = incoming.name ?: lookup?.name
        Text(
            text = title ?: incoming.number.ifEmpty { "不明な発信者" },
            fontSize = if (compact) 20.sp else 28.sp,
            style = MaterialTheme.typography.headlineMedium,
        )
        if (incoming.name == null && lookup != null) {
            // 電話帳に無い相手 (2026-09-25)。検索中→結果へ置き換わる。
            // ⚠web検索の名前は推定なので、電話帳の名前と見分けがつくように出典を添える
            val warn = lookup.verdict == "sales" || lookup.verdict == "scam"
            Spacer(Modifier.size(4.dp))
            Text(
                text = when {
                    lookup.pending -> "番号検索中…"
                    warn -> if (lookup.verdict == "scam") "⚠ 詐欺報告あり" else "⚠ 営業の可能性"
                    lookup.name != null -> "web検索の結果"
                    else -> "情報なし"
                },
                style = MaterialTheme.typography.bodyMedium,
                color = if (warn) Color(0xFFE24B4A) else MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (title != null && incoming.number.isNotEmpty()) {
            Spacer(Modifier.size(4.dp))
            Text(
                text = incoming.number,
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun SectionLabel(text: String) {
    Text(
        text = text,
        style = MaterialTheme.typography.labelSmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
        modifier = Modifier.padding(bottom = 6.dp),
    )
}

@Composable
private fun FragmentCard(f: FragmentApi.Fragment) {
    // kind で色を変える (watcherが info/alert/hint を出す。未知の値は info 扱い)
    val bg = when (f.kind) {
        "alert" -> MaterialTheme.colorScheme.errorContainer
        "hint" -> MaterialTheme.colorScheme.tertiaryContainer
        else -> MaterialTheme.colorScheme.secondaryContainer
    }
    val fg = when (f.kind) {
        "alert" -> MaterialTheme.colorScheme.onErrorContainer
        "hint" -> MaterialTheme.colorScheme.onTertiaryContainer
        else -> MaterialTheme.colorScheme.onSecondaryContainer
    }
    Surface(
        color = bg,
        shape = RoundedCornerShape(10.dp),
        modifier = Modifier
            .fillMaxWidth()
            .padding(bottom = 6.dp),
    ) {
        Column(Modifier.padding(horizontal = 12.dp, vertical = 8.dp)) {
            if (f.title.isNotBlank()) {
                Text(f.title, style = MaterialTheme.typography.labelLarge, color = fg)
            }
            if (f.text.isNotBlank()) {
                Text(
                    f.text,
                    style = MaterialTheme.typography.bodySmall,
                    color = fg,
                    modifier = Modifier
                        .padding(top = 2.dp)
                        .heightIn(max = 96.dp),
                )
            }
        }
    }
}

@Composable
private fun SegmentLine(s: FragmentApi.Segment) {
    val (label, color) = when (s.speaker) {
        "caller" -> "相手" to Color(0xFFBA7517)
        "ai" -> "AI" to Color(0xFF1D9E75)
        "user" -> "自分" to MaterialTheme.colorScheme.primary
        else -> s.speaker to MaterialTheme.colorScheme.onSurfaceVariant
    }
    Row(Modifier.padding(bottom = 4.dp)) {
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = color,
            modifier = Modifier.size(width = 34.dp, height = 18.dp),
        )
        Text(
            text = s.text,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun CallButton(
    label: String,
    icon: androidx.compose.ui.graphics.vector.ImageVector,
    color: Color,
    onClick: () -> Unit,
) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Surface(
            onClick = onClick,
            shape = CircleShape,
            color = color,
            modifier = Modifier.size(66.dp),
        ) {
            Box(contentAlignment = Alignment.Center) {
                Icon(
                    icon,
                    contentDescription = label,
                    tint = Color.White,
                    modifier = Modifier.size(30.dp),
                )
            }
        }
        Spacer(Modifier.size(8.dp))
        Text(
            text = label,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
