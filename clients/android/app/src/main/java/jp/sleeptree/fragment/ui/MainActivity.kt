package jp.sleeptree.fragment.ui

import android.Manifest
import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.rounded.History
import androidx.compose.material.icons.rounded.Home
import androidx.compose.material.icons.rounded.Settings
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import jp.sleeptree.fragment.Prefs
import jp.sleeptree.fragment.ServerState
import jp.sleeptree.fragment.WatchService
import jp.sleeptree.fragment.audio.CallAudio

/**
 * アプリ本体。ホーム / 履歴 / 設定 のタブと、その上に重ねる通話画面・発信画面。
 *
 * ⚠2026-09-24 に管制室の WebView をやめて全部ネイティブにした (ユーザー「webページ経由はなしにして
 *   kotlinでちゃんとUIとかデザインしよう」)。きっかけは通話音声 (audio/CallAudio.kt の注記)。
 *   管制室 (Next.js) は PC 用にそのまま残る。プロンプト・取り次ぎ先など重い設定は PC で行う
 */
class MainActivity : ComponentActivity() {

    // ⚠**launchMode="singleTask" なので、既に起動中だと onCreate ではなく onNewIntent が呼ばれる。**
    //   Intent の中身を State に持たせ、onNewIntent から差し替える (2026-08-01 に実機で発覚した取りこぼし)
    private val overlay = mutableStateOf<Overlay?>(null)
    private val foreground = mutableStateOf(false)

    private fun applyIntent(i: Intent?) {
        val callId = i?.getStringExtra(EXTRA_CALL_ID)
        val room = i?.getStringExtra(EXTRA_ROOM)
        // 応対者として入るか (2026-09-18)。⚠着信に**応答した**ときだけ true。
        //   「通話中」カードから覗くときは聞くだけ (AI はそのまま応対を続ける)
        val operator = i?.getBooleanExtra(EXTRA_OPERATOR, false) ?: false
        // ⚠ロック画面の上に出すのは応答したときだけ (2026-09-19)。それ以外は鍵の下に置く —
        //   会話履歴 (【本人限定】を含む) をロックを解かずに見せない
        setShowWhenLocked(operator)
        setTurnScreenOn(operator)
        // デバッグビルド専用: AI 応対中のロック画面表示を直接開く (`--es debug_live visualizer|chat`)。
        //   ⚠本物の経路 (通知の full-screen intent) はおやすみモード中に OS が止めるので、
        //   描画の確認はこちらで。LiveCallActivity は非公開なので adb から直接は開けない
        if (jp.sleeptree.fragment.BuildConfig.DEBUG) {
            val live = i?.getStringExtra("debug_live")
            val id = ServerState.activeCallId.value
            if (live != null && id != null) {
                startActivity(
                    Intent(this, LiveCallActivity::class.java)
                        .putExtra(LiveCallActivity.EXTRA_CALL_ID, id)
                        .putExtra(LiveCallActivity.EXTRA_MODE, live)
                )
            }
        }
        when {
            operator -> {
                // 取り次ぎはルームが分かっているので、画面より先に音声を繋ぎ始める (相手を待たせない)
                if (room != null) CallAudio.setOperator(this, room, true)
                overlay.value = if (callId != null) Overlay.Call(callId) else Overlay.Joining(room)
            }
            callId != null -> overlay.value = Overlay.Call(callId)
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        applyIntent(intent)
    }

    private val requestPermissions =
        registerForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val prefs = Prefs(this)
        if (!prefs.isConfigured) {
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
            return
        }
        enableEdgeToEdge()
        ensurePermissions()
        ensureFullScreenIntent()
        WatchService.start(this)
        applyIntent(intent)

        setContent {
            FragmentTheme {
                App(
                    prefs = prefs,
                    overlay = overlay.value,
                    setOverlay = { overlay.value = it },
                    foreground = foreground.value,
                )
            }
        }
    }

    override fun onStart() {
        super.onStart()
        foreground.value = true
    }

    override fun onStop() {
        foreground.value = false
        super.onStop()
    }

    /**
     * ⚠**全画面着信の許可**。これが無いと取り次ぎが鳴らない (2026-08-01に実機で判明)。
     *
     * マニフェストの `USE_FULL_SCREEN_INTENT` は granted=true になるが、それとは別に
     * **appop が `default` のままだとOSが全画面表示を拒否する** (`dumpsys package` の
     * `rejectTime` に記録される)。通話アプリなら自動付与されるという前提は
     * Pixel/Android 16 では成立しなかった。設定画面へ送って明示的に許可してもらう。
     */
    private fun ensureFullScreenIntent() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE) return
        val nm = getSystemService(NotificationManager::class.java) ?: return
        if (nm.canUseFullScreenIntent()) return
        runCatching {
            startActivity(
                Intent(
                    Settings.ACTION_MANAGE_APP_USE_FULL_SCREEN_INTENT,
                    Uri.parse("package:$packageName"),
                )
            )
        }
    }

    /** ⚠マイクが無いと「自分が出る」で声が届かない */
    private fun ensurePermissions() {
        val need = mutableListOf<String>()
        if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            need += Manifest.permission.RECORD_AUDIO
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
        ) {
            need += Manifest.permission.POST_NOTIFICATIONS
        }
        if (need.isNotEmpty()) requestPermissions.launch(need.toTypedArray())
    }

    companion object {
        private const val EXTRA_ROOM = "room"
        private const val EXTRA_CALL_ID = "call_id"
        private const val EXTRA_OPERATOR = "operator"

        /**
         * 着信・取り次ぎに応答した。自分が話す (operator) で通話画面を開く。
         * ⚠room しか無い (CALL はそれすら無い) ときは id をこちらで解決する — 呼び出し元 (着信画面) は
         *   前面にいるうちに同期的に遷移させたいので、通信を待たせない (CallCoordinator.answer 参照)
         */
        fun callIntent(context: Context, room: String?): Intent =
            Intent(context, MainActivity::class.java).apply {
                if (room != null) putExtra(EXTRA_ROOM, room)
                putExtra(EXTRA_OPERATOR, true)
            }

        /** 通話中カードなど、id が既に分かっている場合。⚠覗くだけ (operator にはならない) */
        fun callIntentById(context: Context, callId: String): Intent =
            Intent(context, MainActivity::class.java).putExtra(EXTRA_CALL_ID, callId)
    }
}

/** タブの上に重ねる画面 */
sealed interface Overlay {
    data class Call(val id: String) : Overlay

    /** 応答したが通話の id がまだ分からない (Asterisk がブリッジして agent が行を作るまで数秒) */
    data class Joining(val room: String?) : Overlay

    /** number = かけ直しのときの番号 */
    data class Dialer(val number: String = "") : Overlay
}

private enum class Tab(val label: String) { HOME("ホーム"), HISTORY("履歴"), SETTINGS("設定") }

@Composable
private fun App(prefs: Prefs, overlay: Overlay?, setOverlay: (Overlay?) -> Unit, foreground: Boolean) {
    val ctx = LocalContext.current
    var tab by rememberSaveable { mutableStateOf(Tab.HOME) }
    var monitorOutside by rememberSaveable { mutableStateOf(prefs.monitorOutside) }
    var paused by rememberSaveable { mutableStateOf(prefs.paused) }

    // 通話画面の外でも音を流す (設定)。アプリが前面にいる間だけ
    val active by ServerState.activeCall.collectAsState()
    LaunchedEffect(active?.room, foreground, monitorOutside) {
        val a = active
        if (a != null && foreground && monitorOutside) CallAudio.hold(ctx, a.room, "app", true)
        else CallAudio.release("app")
    }

    Box(Modifier.fillMaxSize()) {
        Scaffold(
            containerColor = MaterialTheme.colorScheme.surface,
            bottomBar = {
                NavigationBar(containerColor = MaterialTheme.colorScheme.surfaceContainer) {
                    Tab.entries.forEach { t ->
                        NavigationBarItem(
                            selected = tab == t,
                            onClick = { tab = t },
                            icon = {
                                Icon(
                                    when (t) {
                                        Tab.HOME -> Icons.Rounded.Home
                                        Tab.HISTORY -> Icons.Rounded.History
                                        Tab.SETTINGS -> Icons.Rounded.Settings
                                    },
                                    contentDescription = null,
                                )
                            },
                            label = { Text(t.label) },
                            colors = NavigationBarItemDefaults.colors(
                                indicatorColor = MaterialTheme.colorScheme.primaryContainer,
                            ),
                        )
                    }
                }
            },
        ) { pad ->
            Box(Modifier.padding(pad)) {
                when (tab) {
                    Tab.HOME -> HomeScreen(
                        prefs = prefs,
                        paused = paused,
                        onResume = { paused = false; jp.sleeptree.fragment.DevicePause.set(ctx, false) },
                        onOpenCall = { setOverlay(Overlay.Call(it)) },
                        onOpenHistory = { tab = Tab.HISTORY },
                        onOpenSettings = { tab = Tab.SETTINGS },
                        onDial = { setOverlay(Overlay.Dialer()) },
                    )
                    Tab.HISTORY -> HistoryScreen(prefs = prefs, onOpenCall = { setOverlay(Overlay.Call(it)) })
                    Tab.SETTINGS -> SettingsScreen(
                        prefs = prefs,
                        paused = paused,
                        onPaused = { paused = it; jp.sleeptree.fragment.DevicePause.set(ctx, it) },
                        monitorOutside = monitorOutside,
                        onMonitorOutside = { monitorOutside = it; prefs.monitorOutside = it },
                    )
                }
            }
        }
        BackHandler(enabled = overlay == null && tab != Tab.HOME) { tab = Tab.HOME }

        if (overlay != null) {
            BackHandler { setOverlay(null) }
            Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.surface) {
                when (overlay) {
                    is Overlay.Call -> CallScreen(
                        prefs = prefs,
                        callId = overlay.id,
                        onBack = { setOverlay(null) },
                        onRedial = { setOverlay(Overlay.Dialer(it)) },
                    )
                    is Overlay.Joining -> JoiningScreen(
                        prefs = prefs,
                        room = overlay.room,
                        onJoined = { setOverlay(Overlay.Call(it)) },
                        onGiveUp = { setOverlay(null) },
                    )
                    is Overlay.Dialer -> DialerScreen(
                        prefs = prefs,
                        initial = overlay.number,
                        onClose = { setOverlay(null) },
                        onConnected = { setOverlay(Overlay.Call(it)) },
                    )
                }
            }
        }
    }
}
