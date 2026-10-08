package jp.sleeptree.fragment

import jp.sleeptree.fragment.api.FragmentApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * サーバー (管制室) 側の状態のうち、画面が見たいもの。常駐サービスが書き、画面が読む。
 *
 * ⚠**応答モードの正はサーバー**。2026-08-01 に、端末のスタンバイのタイマーを見て表示を作り
 *   サーバーと食い違ったことがある。タイマー (スタンバイの切り忘れ対策・時間割) は 2026-09-29 に
 *   サーバーへ移したので、端末はもう時限を持たない。
 */
object ServerState {
    private val _answerMode = MutableStateFlow<String?>(null)

    /** away / standby / manual。null = まだサーバーから取れていない */
    val answerMode: StateFlow<String?> = _answerMode

    fun setAnswerMode(mode: String) {
        _answerMode.value = mode
    }

    private val _modeInfo = MutableStateFlow<FragmentApi.ModeInfo?>(null)

    /** 時間割・手動の上書きの状態 (2026-09-29)。null = まだ取れていない */
    val modeInfo: StateFlow<FragmentApi.ModeInfo?> = _modeInfo

    fun setModeInfo(info: FragmentApi.ModeInfo?) {
        if (info != null) _modeInfo.value = info
    }

    /** 「時間割どおり · 17:00 に不在へ」「手動で切り替え中 · 明日 8:00 に時間割へ戻ります」。無ければ null */
    fun modeInfoText(info: FragmentApi.ModeInfo?): String? {
        if (info == null) return null
        val at = info.nextAt?.let { whenText(it) }
        return when (info.source) {
            "manual" ->
                if (info.scheduleEnabled) "手動で切り替え中 · $at に時間割へ戻ります"
                else "$at に${modeLabel(info.nextMode)}へ戻ります"
            "schedule" -> if (at != null) "時間割どおり · $at に${modeLabel(info.nextMode)}へ" else "時間割どおり"
            else -> null
        }
    }

    fun modeLabel(m: String?): String = when (m) {
        "standby" -> "スタンバイ"
        "manual" -> "自分で出る"
        else -> "不在"
    }

    private fun whenText(ms: Long): String {
        val zone = java.time.ZoneId.systemDefault()
        val t = java.time.Instant.ofEpochMilli(ms).atZone(zone)
        val days = java.time.temporal.ChronoUnit.DAYS.between(java.time.LocalDate.now(zone), t.toLocalDate())
        val hm = "%d:%02d".format(t.hour, t.minute)
        return when (days) {
            0L -> hm
            1L -> "明日 $hm"
            else -> "${t.monthValue}/${t.dayOfMonth}(${"月火水木金土日"[t.dayOfWeek.value - 1]}) $hm"
        }
    }

    private val _activeCallId = MutableStateFlow<String?>(null)

    /** AI が応対中の通話の id。null = 通話なし。LiveCallActivity が閉じる合図に使う */
    val activeCallId: StateFlow<String?> = _activeCallId

    private val _activeCall = MutableStateFlow<FragmentApi.ActiveCall?>(null)

    /** いまの通話。ホームの「通話中」と、通話画面の外で音を流すかの判断に使う */
    val activeCall: StateFlow<FragmentApi.ActiveCall?> = _activeCall

    fun setActiveCall(call: FragmentApi.ActiveCall?) {
        _activeCall.value = call
        _activeCallId.value = call?.id
    }
}
