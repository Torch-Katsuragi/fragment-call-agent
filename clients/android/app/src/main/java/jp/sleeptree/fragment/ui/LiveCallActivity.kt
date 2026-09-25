package jp.sleeptree.fragment.ui

import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.Canvas
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
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import jp.sleeptree.fragment.Prefs
import jp.sleeptree.fragment.ServerState
import jp.sleeptree.fragment.api.FragmentApi
import jp.sleeptree.fragment.audio.CallAudio
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlin.math.PI
import kotlin.math.cos
import kotlin.math.sin

/**
 * AI が応対している間、ロック画面の上に出す表示 (2026-09-19)。
 *
 * ユーザーの指示で 3 パターンを端末設定 (Prefs.liveDisplay) で選ぶ:
 *   none       — 何も出さない (この Activity は起動されない)
 *   visualizer — 相手の名前 + 音に合わせて伸び縮みする円状のバー
 *   chat       — 会話ストリームだけの吹き出し
 *
 * ⚠起動は「通話中」通知の full-screen intent 経由 (CallNotifications.showOngoing)。
 *   常駐サービスから直接 startActivity すると、バックグラウンドからの Activity 起動として
 *   OS に止められる。full-screen intent はロック中・画面OFFのときだけ画面を出し、
 *   使用中ならヘッドアップ通知に化ける (それでよい — 使っている最中に画面を奪わない)。
 * ⚠2026-09-24 にネイティブで描き直した (前は管制室の /live を WebView で出していた)。
 *   音を流すかは「通話画面の外でも音を流す」設定に従う。波形は音を受けなくても動く
 *   (サーバーの話者レベルで動かす — CallAudio の注記)。
 * ⚠フラグメント (【本人限定】を含みうる) はロック画面に出さない。会話だけ
 */
class LiveCallActivity : ComponentActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setShowWhenLocked(true)
        setTurnScreenOn(true)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)

        val prefs = Prefs(this)
        val callId = intent?.getStringExtra(EXTRA_CALL_ID).orEmpty()
        val mode = intent?.getStringExtra(EXTRA_MODE) ?: prefs.liveDisplay
        if (callId.isEmpty() || mode == Prefs.LIVE_NONE || !prefs.isConfigured) {
            finish()
            return
        }

        setContent {
            FragmentTheme {
                val active by ServerState.activeCallId.collectAsState()
                // 通話が終わった (別の通話に変わった) ら閉じる
                LaunchedEffect(active) {
                    if (active != callId) finish()
                }
                LiveScreen(prefs, callId, mode, onClose = { finish() })
            }
        }
    }

    companion object {
        const val EXTRA_CALL_ID = "call_id"
        const val EXTRA_MODE = "mode"
    }
}

private val Ink = Color(0xFF0B0F0D)
private val AiGreen = Color(0xFF5CCBA3)
private val CallerAmber = Color(0xFFF0B25E)

@Composable
private fun LiveScreen(prefs: Prefs, callId: String, mode: String, onClose: () -> Unit) {
    val ctx = LocalContext.current
    val api = remember(prefs) { FragmentApi(prefs) }
    var call by remember { mutableStateOf<FragmentApi.Call?>(null) }
    LaunchedEffect(callId) {
        while (true) {
            withContext(Dispatchers.IO) { api.call(callId) }?.let { call = it }
            // 波形だけなら名前が取れれば足りる。会話は発話ごとに取り直す
            delay(if (mode == Prefs.LIVE_CHAT) 1500 else 10_000)
        }
    }
    val c = call
    DisposableEffect(c?.room) {
        if (c != null) CallAudio.hold(ctx, c.room, "live", audible = prefs.monitorOutside)
        onDispose { CallAudio.release("live") }
    }

    Box(
        Modifier
            .fillMaxSize()
            .background(Ink)
            .safeDrawingPadding()
    ) {
        if (mode == Prefs.LIVE_CHAT) {
            LiveChat(c, Modifier.padding(top = 56.dp))
        } else {
            LiveVisualizer(c)
        }
        TextButton(onClick = onClose, modifier = Modifier.align(Alignment.TopEnd).padding(8.dp)) {
            Text("閉じる", color = Color.White.copy(alpha = 0.8f))
        }
        Row(
            Modifier
                .align(Alignment.TopStart)
                .padding(start = 20.dp, top = 20.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            LiveDot()
            Spacer(Modifier.size(8.dp))
            Text("AIが応対中  ", style = MaterialTheme.typography.labelLarge, color = Color.White.copy(alpha = 0.8f))
            if (c != null) Elapsed(c.startedAt, color = Color.White.copy(alpha = 0.6f))
        }
    }
}

/** 名前 + 円状に並んだバーが声に合わせて伸び縮みする */
@Composable
private fun LiveVisualizer(c: FragmentApi.Call?) {
    val levels by CallAudio.levels.collectAsState()
    // なめらかにする: 立ち上がりは速く、減衰はゆっくり。話者レベルは 0.1〜0.5 秒おきにしか来ないので
    var caller by remember { mutableFloatStateOf(0f) }
    var ai by remember { mutableFloatStateOf(0f) }
    var t by remember { mutableFloatStateOf(0f) }
    LaunchedEffect(Unit) {
        var last = 0L
        while (true) {
            withFrameNanos { now ->
                val dt = if (last == 0L) 0f else (now - last) / 1e9f
                last = now
                t += dt
                fun follow(cur: Float, target: Float) =
                    cur + (target - cur) * (if (target > cur) 0.35f else 0.06f)
                caller = follow(caller, levels.caller)
                ai = follow(ai, levels.ai)
            }
        }
    }

    Column(
        Modifier.fillMaxSize(),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Box(Modifier.size(300.dp), contentAlignment = Alignment.Center) {
            Canvas(Modifier.fillMaxSize()) {
                val n = 72
                val center = Offset(size.width / 2, size.height / 2)
                val r0 = size.minDimension * 0.28f
                val maxLen = size.minDimension * 0.2f
                val level = maxOf(caller, ai)
                val share = if (caller + ai > 0.001f) caller / (caller + ai) else 0f
                val color = lerp(AiGreen, CallerAmber, share)
                for (i in 0 until n) {
                    val a = (i / n.toFloat()) * 2 * PI.toFloat() - PI.toFloat() / 2
                    // バーごとの揺らぎ。1 つの音量から「声らしい」凹凸を作る
                    val wobble = 0.55f + 0.45f * sin(i * 0.9f + t * 5.3f) * cos(i * 0.37f - t * 3.1f)
                    val len = 4.dp.toPx() + maxLen * (level * 1.6f).coerceAtMost(1f) * wobble
                    val dir = Offset(cos(a), sin(a))
                    drawLine(
                        color = color.copy(alpha = 0.35f + 0.65f * (level * 2f).coerceAtMost(1f)),
                        start = center + dir * r0,
                        end = center + dir * (r0 + len),
                        strokeWidth = 3.dp.toPx(),
                        cap = StrokeCap.Round,
                    )
                }
            }
            Avatar(c?.name, size = 96.dp, container = Color.White.copy(alpha = 0.08f), content = Color.White)
        }
        Spacer(Modifier.height(24.dp))
        Text(c?.displayName ?: " ", style = MaterialTheme.typography.headlineMedium, color = Color.White)
        if (c?.name != null && c.number != null) {
            Text(c.number, style = MaterialTheme.typography.bodyMedium, color = Color.White.copy(alpha = 0.55f))
        }
    }
}

/** 会話だけ (フラグメントは出さない) */
@Composable
private fun LiveChat(c: FragmentApi.Call?, modifier: Modifier = Modifier) {
    val interim by CallAudio.interim.collectAsState()
    val segs = c?.segments.orEmpty().filter { it.speaker != "whisper" }
    val done = segs.takeLast(6).map { it.text.trim() }.toSet()
    val lines = segs.map { it.speaker to it.text } +
        interim.filter { it.text.isNotBlank() && it.text.trim() !in done }.map { it.speaker to it.text }
    val state = rememberLazyListState()
    LaunchedEffect(lines.size, lines.lastOrNull()?.second) {
        if (lines.isNotEmpty()) state.scrollToItem(lines.size - 1)
    }
    LazyColumn(
        modifier.fillMaxWidth(),
        state = state,
        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        items(lines.size) { i ->
            val (who, text) = lines[i]
            val left = who == "caller"
            Column(Modifier.fillMaxWidth(), horizontalAlignment = if (left) Alignment.Start else Alignment.End) {
                Box(
                    Modifier
                        .widthIn(max = 300.dp)
                        .clip(RoundedCornerShape(18.dp))
                        .background(if (left) Color.White.copy(alpha = 0.10f) else AiGreen.copy(alpha = 0.22f))
                        .padding(horizontal = 14.dp, vertical = 9.dp)
                ) {
                    Text(text, style = MaterialTheme.typography.bodyLarge, color = if (left) CallerAmber.copy(alpha = 0.95f) else Color.White)
                }
            }
        }
    }
}
