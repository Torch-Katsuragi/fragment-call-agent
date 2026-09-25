package jp.sleeptree.fragment.audio

import android.annotation.SuppressLint
import android.content.Context
import android.os.PowerManager
import android.util.Log
import com.twilio.audioswitch.AudioDevice
import io.livekit.android.ConnectOptions
import io.livekit.android.LiveKit
import io.livekit.android.RoomOptions
import io.livekit.android.events.RoomEvent
import io.livekit.android.room.Room
import io.livekit.android.room.track.RemoteTrackPublication
import io.livekit.android.room.track.Track
import jp.sleeptree.fragment.Prefs
import jp.sleeptree.fragment.api.FragmentApi
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/**
 * 通話の音声 (2026-09-24 に WebView からネイティブへ移した)。
 *
 * ⚠移した理由: WebView の <audio> は「メディア」の音として鳴るが、マイクを掴むと Android は
 *   通話モードに入り、音量ボタンは「通話」の音量を動かす。相手の声が -39 dB のまま上げられず
 *   (ユーザー「電話相手の声が聞こえない」)、受話口・Bluetooth・近接センサーも扱えなかった。
 *   LiveKit Android SDK は WebRTC の通話経路で鳴らし、AudioSwitch で出力先を選ぶ = 普通の電話と同じ。
 *
 * 形: ルームへの接続はプロセスに 1 本。画面は「持つ (hold)」「離す (release)」だけ言い、
 *   繋ぐか・音を出すかはここがまとめて決める。
 *   - 誰かが持っている or 自分が応対中 → 繋ぐ
 *   - 音を出す持ち手がいる or 応対中 → 音声トラックを購読する (出さないときは購読しない)
 *   ⚠音を出さない持ち手 (ロック画面の波形) は購読しない。波形は participant.audioLevel
 *     (サーバーが配る話者レベル) で動くので、音を受けなくても動く。購読しなければ
 *     オーディオフォーカスも取らない = 音楽を止めない
 * ⚠応対の交代 (operator ↔ 聞くだけ) はトークンの権限が違うので**繋ぎ直す**。管制室と同じ作法で、
 *   agent は operator- の出入りで AI を下げる/戻す
 */
@SuppressLint("StaticFieldLeak") // 持つのは applicationContext だけ
object CallAudio {

    enum class Route { EARPIECE, SPEAKER, BLUETOOTH, WIRED }

    data class State(
        /** 繋いでいる (繋ごうとしている) ルーム。null = 繋いでいない */
        val room: String? = null,
        val connected: Boolean = false,
        /** 自分が応対中 (マイクを出している) */
        val operator: Boolean = false,
        /** 音を出しているか */
        val audible: Boolean = false,
        val muted: Boolean = false,
        val route: Route? = null,
        val routes: List<Route> = emptyList(),
        /** 繋げなかった理由。画面に一行出す */
        val error: String? = null,
    )

    /** 0..1。サーバーが配る話者レベル (約 0.1〜0.5 秒ごとに更新される) */
    data class Levels(val caller: Float = 0f, val ai: Float = 0f, val me: Float = 0f)

    /** 発話途中の文字 (まだ DB に入っていない)。speaker: caller / ai / user */
    data class Interim(val key: String, val speaker: String, val text: String, val final: Boolean, val at: Long)

    private val _state = MutableStateFlow(State())
    val state: StateFlow<State> = _state

    private val _levels = MutableStateFlow(Levels())
    val levels: StateFlow<Levels> = _levels

    private val _interim = MutableStateFlow<List<Interim>>(emptyList())
    val interim: StateFlow<List<Interim>> = _interim

    private const val TAG = "CallAudio"
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val mutex = Mutex()

    private var app: Context? = null
    private var targetRoom: String? = null
    private var wantOperator = false
    /** 持ち手 → 音を出したいか */
    private val holders = mutableMapOf<String, Boolean>()

    private var room: Room? = null
    private var roomName: String? = null
    private var roomOperator = false
    private var jobs = mutableListOf<Job>()
    private var proximity: PowerManager.WakeLock? = null

    /** 画面がルームを持つ。audible = 音を出したい (通話画面は true、ロック画面の波形は設定次第) */
    fun hold(context: Context, room: String, key: String, audible: Boolean) {
        app = context.applicationContext
        if (targetRoom != room) {
            // 別の通話に移った。前の通話の持ち手と応対は引き継がない
            holders.clear()
            wantOperator = false
        }
        targetRoom = room
        holders[key] = audible
        reconcile()
    }

    fun release(key: String) {
        if (holders.remove(key) != null) reconcile()
    }

    /** 自分が出る / AI に任せる */
    fun setOperator(context: Context, room: String, on: Boolean) {
        app = context.applicationContext
        if (targetRoom != room) holders.clear()
        targetRoom = room
        wantOperator = on
        reconcile()
    }

    /** 通話が終わった。全部離す */
    fun endAll() {
        holders.clear()
        wantOperator = false
        targetRoom = null
        reconcile()
    }

    fun setMuted(muted: Boolean) {
        val r = room ?: return
        scope.launch {
            runCatching { r.localParticipant.setMicrophoneEnabled(!muted) }
                .onSuccess { _state.value = _state.value.copy(muted = muted) }
                .onFailure { Log.w(TAG, "mute failed", it) }
        }
    }

    fun selectRoute(route: Route) {
        val h = room?.audioSwitchHandler ?: return
        val d = h.availableAudioDevices.firstOrNull { it.toRoute() == route } ?: return
        h.selectDevice(d)
    }

    private fun reconcile() {
        scope.launch { mutex.withLock { apply() } }
    }

    private suspend fun apply() {
        val ctx = app ?: return
        val want = targetRoom?.takeIf { holders.isNotEmpty() || wantOperator }
        val audible = wantOperator || holders.values.any { it }

        if (want != roomName || (want != null && wantOperator != roomOperator)) {
            teardown()
            if (want != null) connect(ctx, want, wantOperator)
        }
        val r = room ?: run {
            _state.value = State(error = _state.value.error.takeIf { want != null })
            updateProximity(ctx)
            return
        }
        setAudible(r, audible)
        _state.value = _state.value.copy(audible = audible)
        updateProximity(ctx)
    }

    private suspend fun connect(ctx: Context, name: String, operator: Boolean) {
        _state.value = State(room = name, operator = operator)
        val tok = withContext(Dispatchers.IO) { FragmentApi(Prefs(ctx)).roomToken(name, operator) }
        if (tok == null) {
            _state.value = State(error = "接続できませんでした")
            return
        }
        val r = LiveKit.create(ctx, RoomOptions(adaptiveStream = false, dynacast = false))
        r.audioSwitchHandler?.apply {
            // 応対中は受話口、聞くだけならスピーカーを先に。イヤホン・Bluetooth があればそちら
            preferredDeviceList = if (operator) {
                listOf(
                    AudioDevice.BluetoothHeadset::class.java,
                    AudioDevice.WiredHeadset::class.java,
                    AudioDevice.Earpiece::class.java,
                    AudioDevice.Speakerphone::class.java,
                )
            } else {
                listOf(
                    AudioDevice.BluetoothHeadset::class.java,
                    AudioDevice.WiredHeadset::class.java,
                    AudioDevice.Speakerphone::class.java,
                    AudioDevice.Earpiece::class.java,
                )
            }
            audioDeviceChangeListener = { devices, selected ->
                _state.value = _state.value.copy(
                    route = selected?.toRoute(),
                    routes = devices.mapNotNull { it.toRoute() }.distinct(),
                )
                updateProximity(ctx)
            }
        }
        room = r
        roomName = name
        roomOperator = operator
        jobs += scope.launch { watch(r) }
        registerTranscripts(r)
        try {
            // ⚠自動購読は切る。音を出すかどうかで購読を入れ切りする (setAudible)
            r.connect(tok.url, tok.token, ConnectOptions(autoSubscribe = false))
            if (operator) r.localParticipant.setMicrophoneEnabled(true)
            _state.value = _state.value.copy(connected = true, operator = operator, muted = false, error = null)
            Log.i(TAG, "connected room=$name operator=$operator")
        } catch (e: Exception) {
            Log.w(TAG, "connect failed", e)
            teardown()
            _state.value = State(error = "接続できませんでした")
            return
        }
        jobs += scope.launch { pollLevels(r) }
    }

    private fun teardown() {
        jobs.forEach { it.cancel() }
        jobs.clear()
        room?.let {
            runCatching { it.disconnect() }
            runCatching { it.release() }
        }
        room = null
        roomName = null
        roomOperator = false
        _levels.value = Levels()
        _interim.value = emptyList()
    }

    /**
     * 音を出す/出さない。購読の入れ切りと、出力の握り (AudioSwitch = 通話モード・フォーカス) を揃える。
     * ⚠出さないのに AudioSwitch を握ったままだと、ロック画面の波形を出しただけで音楽が止まる
     */
    private fun setAudible(r: Room, audible: Boolean) {
        r.remoteParticipants.values.forEach { p ->
            p.audioTrackPublications.forEach { (pub, _) ->
                (pub as? RemoteTrackPublication)?.let { if (it.subscribed != audible) it.setSubscribed(audible) }
            }
        }
        val h = r.audioSwitchHandler ?: return
        if (audible) h.start() else h.stop()
    }

    private suspend fun watch(r: Room) {
        r.events.events.collect { e ->
            when (e) {
                // 後から出てきた音声 (AI の声・相手の声) も、音を出しているなら拾う
                is RoomEvent.TrackPublished -> {
                    val pub = e.publication as? RemoteTrackPublication
                    if (pub != null && pub.kind == Track.Kind.AUDIO && _state.value.audible) pub.setSubscribed(true)
                }
                is RoomEvent.Disconnected -> {
                    Log.i(TAG, "disconnected: ${e.reason}")
                    _state.value = _state.value.copy(connected = false)
                }
                is RoomEvent.Reconnected -> _state.value = _state.value.copy(connected = true)
                else -> Unit
            }
        }
    }

    private suspend fun pollLevels(r: Room) {
        while (scope.isActive) {
            var caller = 0f
            var ai = 0f
            r.remoteParticipants.values.forEach { p ->
                val id = p.identity?.value.orEmpty()
                when {
                    id.startsWith("sip_") -> caller = maxOf(caller, p.audioLevel)
                    // 管制室・他の端末の聞き手は数えない。音声を出している残り = AI
                    id.startsWith("dashboard-") || id.startsWith("operator-") -> Unit
                    p.audioTrackPublications.isNotEmpty() -> ai = maxOf(ai, p.audioLevel)
                }
            }
            _levels.value = Levels(caller, ai, r.localParticipant.audioLevel)
            // 確定して DB に入ったはずの途中表示は落とす (通話画面のポーリングは 1.5 秒)
            val now = System.currentTimeMillis()
            _interim.value.let { list ->
                val kept = list.filter { !it.final || now - it.at < 3000 }
                if (kept.size != list.size) _interim.value = kept
            }
            delay(50)
        }
    }

    /**
     * 発話途中の文字 (livekit-agents の lk.transcription)。管制室の useTranscriptions と同じもの。
     * ⚠誰の発話かは `lk.transcribed_track_id` (文字起こしした音声トラック) の持ち主で決める。
     *   送り主 (identity) は相手の発話でも agent になる — 送り主で決めていたら、相手の言葉が
     *   AI の吹き出しに出た (2026-09-24 実機)
     */
    private fun registerTranscripts(r: Room) {
        runCatching {
            r.registerTextStreamHandler("lk.transcription") { receiver, identity ->
                val info = receiver.info
                val track = info.attributes["lk.transcribed_track_id"]
                val who = track?.let { tid ->
                    r.remoteParticipants.values.firstOrNull { it.trackPublications.containsKey(tid) }?.identity?.value
                        ?: r.localParticipant.identity?.value?.takeIf { r.localParticipant.trackPublications.containsKey(tid) }
                } ?: identity.value
                val speaker = when {
                    who.startsWith("sip_") -> "caller"
                    who.startsWith("operator-") -> "user"
                    else -> "ai"
                }
                val key = info.attributes["lk.segment_id"] ?: info.id
                val final = info.attributes["lk.transcription_final"] == "true"
                jobs += scope.launch {
                    var text = ""
                    runCatching {
                        receiver.flow.collect { chunk ->
                            text += chunk
                            put(Interim(key, speaker, text, final, System.currentTimeMillis()))
                        }
                    }
                }
            }
        }.onFailure { Log.w(TAG, "transcription handler failed", it) }
    }

    private fun put(item: Interim) {
        val list = _interim.value
        val i = list.indexOfFirst { it.key == item.key }
        _interim.value = if (i < 0) list + item else list.toMutableList().also { it[i] = item }
    }

    /**
     * 受話口で話している間は、耳に当てたら画面を消す (普通の電話と同じ)。
     * ⚠スピーカー・イヤホンのときは消さない — 画面を見ながら話せるように
     */
    private fun updateProximity(ctx: Context) {
        val s = _state.value
        val want = s.operator && s.connected && s.route == Route.EARPIECE
        val held = proximity?.isHeld == true
        if (want && !held) {
            val pm = ctx.getSystemService(PowerManager::class.java) ?: return
            if (!pm.isWakeLockLevelSupported(PowerManager.PROXIMITY_SCREEN_OFF_WAKE_LOCK)) return
            proximity = pm.newWakeLock(PowerManager.PROXIMITY_SCREEN_OFF_WAKE_LOCK, "fragment:proximity")
                .also { it.acquire(3 * 3600_000L) }
        } else if (!want && held) {
            runCatching { proximity?.release(PowerManager.RELEASE_FLAG_WAIT_FOR_NO_PROXIMITY) }
            proximity = null
        }
    }

    private fun AudioDevice.toRoute(): Route? = when (this) {
        is AudioDevice.Earpiece -> Route.EARPIECE
        is AudioDevice.Speakerphone -> Route.SPEAKER
        is AudioDevice.BluetoothHeadset -> Route.BLUETOOTH
        is AudioDevice.WiredHeadset -> Route.WIRED
        else -> null
    }
}
