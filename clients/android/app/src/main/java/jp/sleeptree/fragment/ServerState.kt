package jp.sleeptree.fragment

import jp.sleeptree.fragment.api.FragmentApi
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/**
 * サーバー (管制室) 側の状態のうち、画面が見たいもの。常駐サービスが書き、画面が読む。
 *
 * ⚠**応答モードの正はサーバー**。端末の `Prefs.standbyUntil` は「いつ自動で解除するか」の
 *   タイマーであって、いま何のモードかを答えるものではない。
 *   最初これを取り違えていて、**サーバーがスタンバイなのにアプリの上部バーが「不在」と
 *   表示していた** (2026-08-01の実機で発覚)。管制室から切り替えると必ずズレる。
 */
object ServerState {
    private val _answerMode = MutableStateFlow<String?>(null)

    /** away / standby / manual。null = まだサーバーから取れていない */
    val answerMode: StateFlow<String?> = _answerMode

    fun setAnswerMode(mode: String) {
        _answerMode.value = mode
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
