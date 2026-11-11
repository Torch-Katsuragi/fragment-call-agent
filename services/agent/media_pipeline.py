# -*- coding: utf-8 -*-
"""音声メディアパイプライン (VAD / STT / TTS / AgentSession の構成と保留音再生)。"""

import logging
import os
from pathlib import Path

from google.cloud import texttospeech as gcloud_texttospeech
from google.genai import types as genai_types
from livekit import rtc
from livekit.agents import AgentSession
from livekit.agents import tts as tts_mod
from livekit.plugins import deepgram, google, silero
from livekit.plugins.google.realtime import realtime_api as _google_realtime

import vertex
from aivis_tts import DEFAULT_MODEL_UUID as AIVIS_DEFAULT_MODEL
from aivis_tts import AivisTTS
from voicevox_tts import VoicevoxTTS

# ⚠プラグインのモデル名の照合を外す (2026-10-08)。プラグインは gemini-3.8-live を「Gemini API 専用」と
#   決め打ちで持っていて vertexai=True だと止めるが、Vertex AI (us-central1) でも実際に動く (音声が返るのを実測)。
#   照合は名前の一覧との完全一致だけで、他の振る舞いには関わらない
_google_realtime._validate_model_api_match = lambda model, use_vertexai: None

logger = logging.getLogger("call-agent")

# 無言検知のしきい値 (2026-07-31)。電話で5秒の沈黙は長い — 相手が黙ったら一度促し、
# それでも黙っていたら切る。⚠1回で切らないのは、電波が悪いだけの人や機械が苦手で
# 言葉に詰まる人を切ってしまうため
SILENCE_SEC = float(os.environ.get("SILENCE_SEC") or "5")

# ===== 保留 (2026-09-26、presence.py) =====
# 会話中の端末が 0 台で AI 応答がオフ = 保留。保留音を流し、長引いたら AI が引き取る —
# 会話中の端末が電池切れ・圏外で消えても、相手を保留音のまま放置しない
HOLD_TIMEOUT_SEC = float(os.environ.get("HOLD_TIMEOUT_SEC") or "60")
# 本人が受けた通話・発信で、本人の端末がルームに入ってくるまでの猶予。過ぎたら保留として扱う
CONNECT_GRACE_SEC = 15.0
# 会話中の最後の 1 人が抜けてから保留と見なすまでの間。「AIに任せる」は AI 応答のオンと
# 退室が別々に届くので、順番が前後すると一瞬だけ保留音が鳴る
LEAVE_SETTLE_SEC = 1.2
HOLD_MUSIC = str(Path(__file__).with_name("assets") / "hold.wav")
HOLD_TIMEOUT_LINE = "大変お待たせしております。担当の者がつかまりませんので、AIが代わりにご用件を伺います。"
# まだ名乗っていない通話 (本人が受けた通話) に AI が入るときの一言。AI であることは必ず言う
TAKEOVER_LINE = "ここからはAIが代わってお話を伺います。"

_hold_frames: list[rtc.AudioFrame] = []


async def _load_hold_music() -> list[rtc.AudioFrame]:
    """保留音をメモリに展開する (プロセスで 1 回)。

    ⚠ファイルのまま BackgroundAudioPlayer に渡すと**無音になる** (2026-09-26 に VM で実測、RMS 0)。
    ミキサーは 1 フレームを 200ms しか待たず、待ちきれないと __anext__ を cancel する。2 コアの VM では
    ファイルの読み始めがそれより遅く、cancel でジェネレータごと閉じて、以後は無音を混ぜ続けていた。
    展開済みのフレームを配るだけなら待たせない
    """
    if not _hold_frames:
        from livekit.agents.utils.audio import audio_frames_from_file

        async for f in audio_frames_from_file(HOLD_MUSIC, sample_rate=48000, num_channels=1):
            _hold_frames.append(f)
    return _hold_frames


async def _hold_music_loop():
    frames = await _load_hold_music()
    while True:
        for f in frames:
            yield f


_PREWARMED_VAD = None


def _make_live_vad():
    """live 構成の VAD。silero (既定) か webrtc (軽い、LIVE_VAD=webrtc)。
    同じ 97 秒の通話音声で CPU は silero 10.1 秒 / webrtc 1.4 秒 (2026-10-02、手元の PC)"""
    global _PREWARMED_VAD
    if os.environ.get("LIVE_VAD", "silero") == "webrtc":
        from webrtc_vad import WebRTCVAD

        return WebRTCVAD()
    if _PREWARMED_VAD is None:
        _PREWARMED_VAD = silero.VAD.load()
    return _PREWARMED_VAD


def _make_stt():
    """STTは STT_PROVIDER 環境変数で切り替え (デフォルト: deepgram)。

    - deepgram: nova-3。確定が速い (0.3〜0.4s) が、日本語の電話音声 (8kHz) で誤認識が目立つ
                (2026-07-18実機:「この後どうすか」→「この後道どうすか。ばあたり。」)
    - google:   Cloud Speech-to-Text v2 ストリーミング。認証はCloud TTSと同じSA鍵。
                モデルは GOOGLE_STT_MODEL で切替 (latest_long / telephony / chirp_2 等。
                chirp系はストリーミング対応リージョンが限られる → GOOGLE_STT_LOCATION)
    """
    if os.environ.get("STT_PROVIDER", "deepgram") == "google":
        end_timeout = float(os.environ.get("GOOGLE_STT_END_TIMEOUT") or "0")
        extra = {"speech_end_timeout": end_timeout} if end_timeout > 0 else {}
        creds = os.environ.get("GOOGLE_TTS_CREDENTIALS", "/secrets/callagent-tts.json")
        if creds and Path(creds).is_file():
            extra["credentials_file"] = creds
        return google.STT(
            languages="ja-JP",
            detect_language=False,
            model=os.environ.get("GOOGLE_STT_MODEL", "latest_long"),
            location=os.environ.get("GOOGLE_STT_LOCATION", "global"),
            **extra,
        )
    return deepgram.STT(model="nova-3", language="ja")


def _make_one_tts(provider: str):
    """プロバイダ名1つからTTSを作る (フォールバックの組み立ては _make_tts 側でやる)。"""
    if provider == "aivis":
        # Aivis Cloud API (声=コハク)。初回バイト0.08〜0.12秒の実測 → 詳細は aivis_tts.py
        return AivisTTS(
            api_key=os.environ.get("AIVIS_API_KEY", ""),
            # ⚠get()の既定値ではなく or を使う。composeが `${VAR:-}` で**空文字**を
            #   渡してくるため、キーは存在してしまい既定値が効かない (2026-07-30)
            model_uuid=os.environ.get("AIVIS_MODEL_UUID") or AIVIS_DEFAULT_MODEL,
            speaking_rate=float(os.environ.get("AIVIS_SPEAKING_RATE") or "1.2"),
            # 既定2.0。1.0だと電話で「声が小さい」= Googleより8dB低かった (aivis_tts.py参照)
            volume=float(os.environ.get("AIVIS_VOLUME") or "2.0"),
        )
    if provider == "google":
        return _make_google_tts()
    if provider == "elevenlabs":
        # 日本語の漢字読み間違いが多く不採用 (2026-07-18)。コードは切替可能なまま残置
        from livekit.plugins import elevenlabs

        return elevenlabs.TTS(model="eleven_flash_v2_5", language="ja")
    if provider == "voicevox":
        return VoicevoxTTS(
            base_url=os.environ.get("VOICEVOX_URL", "http://voicevox:50021"),
            speaker=int(os.environ.get("VOICEVOX_SPEAKER", "2")),
        )
    # ⚠3.8 は livekit-plugins-google 1.8.3 以上が要る (1.8.2 だと instructions を読み上げる。
    #   requirements.txt 参照)。GEMINI_TTS_MODEL で戻せる
    return google.beta.GeminiTTS(
        model=os.environ.get("GEMINI_TTS_MODEL", "gemini-3.8-flash-tts"),
        voice_name=os.environ.get("GEMINI_VOICE", "Leda"),
        instructions="電話の受付担当として、丁寧で自然な日本語で話してください。",
    )


# 管制室の設定画面に出す選択肢。ここに無い値が来たら gemini 扱いになる
TTS_CHOICES = ("aivis", "google", "gemini", "elevenlabs", "voicevox")


def _make_tts(primary: str | None = None, fallback: str | None = None):
    """メイン/サブの2段構えでTTSを作る。

    引数は管制室の設定 (settingsテーブル) から渡される。None なら環境変数の既定を使う。
    ⚠**サブを置く理由**: 外部APIに声を預けると、落ちたときに電話が無言になる。
      通話で一番痛い障害がそれなので、合成が失敗したら FallbackAdapter が次に切り替える。
      サブ無し (None/same) でも動くが、その場合は無言になるリスクを受け入れることになる。

    - google:   Google Cloud TTS StreamingSynthesize (Chirp 3: HD)。真のストリーミングで
                初動が速く、日本語の読み(漢字)もGoogle品質。認証はサービスアカウント鍵
                (GOOGLE_TTS_CREDENTIALS、GOOGLE_API_KEYでは動かない)
    - gemini:   Gemini TTS (gemini-3.8-flash-tts)。既存のGOOGLE_API_KEYで動く。
                声は GEMINI_TTS_VOICE (Live APIと同じ30声: Leda/Kore/Aoede/Zephyr等)。
                非ストリーミングで初動1〜2秒
    - voicevox: ローカルのVOICEVOX ENGINE (compose --profile voicevox で起動)。
                無料だがCPU合成が重い — このPC(省電力CPU+WSL2)では1文約6秒で電話には不適
                (2026-07-16実測)。強いマシンに載せ替えたら再検討
    """
    main = primary or os.environ.get("TTS_PROVIDER", "gemini")
    sub = fallback if fallback is not None else os.environ.get("TTS_FALLBACK", "google")
    voice = _make_one_tts(main)
    if not sub or sub == main or sub == "none":
        logger.info("TTS: %s (サブなし)", main)
        return voice
    try:
        backup = _make_one_tts(sub)
    except Exception:
        # サブの構成に失敗しても通話は止めない (メインが生きているなら喋れる)
        logger.exception("TTSのサブ(%s)を構成できなかった — メイン単体で続行", sub)
        return voice
    logger.info("TTS: %s (サブ: %s)", main, sub)
    return tts_mod.FallbackAdapter([voice, backup])


def _make_google_tts():
    """Google Cloud TTS (Chirp3-HD)。aivis のフォールバック先としても使う。
    鍵ファイルが存在しない環境 (Cloud Run 等) では ADC (サービスアカウント) を使う。"""
    streaming = os.environ.get("GOOGLE_TTS_STREAMING", "0") == "1"
    kwargs = {}
    if not streaming:
        # ⚠一括合成APIはPCM(デフォルト)非対応 — LINEAR16必須
        # (PCMのままだと全合成が400エラーで無音になる。2026-07-18実機で被弾)
        kwargs["audio_encoding"] = gcloud_texttospeech.AudioEncoding.LINEAR16
    creds = os.environ.get("GOOGLE_TTS_CREDENTIALS", "/secrets/callagent-tts.json")
    if creds and Path(creds).is_file():
        kwargs["credentials_file"] = creds
    return google.TTS(
        language="ja-JP",
        voice_name=os.environ.get("GOOGLE_TTS_VOICE", "ja-JP-Chirp3-HD-Leda"),
        use_streaming=streaming,
        **kwargs,
    )


def _make_session(tts_primary: str | None = None, tts_fallback: str | None = None) -> AgentSession:
    """AIコアは AI_CORE 環境変数で切替 (デフォルト: live)。

    - live:    Gemini Live API。音声→音声で速い・自然。声は GEMINI_VOICE
    - cascade: Deepgram STT → Gemini 3.5 Flash (thinking無効) → _make_tts()。
               賢さ優先だがターン遅延が大きい (2026-07-16実測で体感NG)
    """
    # ⚠割り込みの閾値を上げる (2026-07-30の実機テストで判明)。
    # 既定 (0.5秒・語数制限なし) だと、相手が電話に向けてではなく横で言った独り言
    # (「お礼言われた」) でAIが文の途中で黙り、相手からは「え、消えた」と見える。
    # 実際にそう言われた。相槌 (「うん」「はい」「ええ」) で止まるのも同じ理屈。
    # 0.8秒 + 2語以上を要求すると、相槌では止まらず、本当の割り込みには従う。
    # ⚠上げすぎると「もういいです」で切りたい相手を黙らせる無礼な機械になるので、
    #   自動テスト (相手役AIのシナリオ) で両方向を確認してから動かすこと
    interrupt_sec = float(os.environ.get("MIN_INTERRUPTION_SEC", "0.8"))
    interrupt_words = int(os.environ.get("MIN_INTERRUPTION_WORDS", "2"))
    if os.environ.get("AI_CORE", "live") == "cascade":
        return AgentSession(
            user_away_timeout=SILENCE_SEC,
            vad=silero.VAD.load(),
            stt=_make_stt(),
            min_interruption_duration=interrupt_sec,
            # 語数判定はSTTの結果を使うのでcascadeでのみ有効
            min_interruption_words=interrupt_words,
            # STT暫定結果でLLM+TTSを見切り発車し、確定と一致すればそのまま採用
            # (Google STTの確定待ち約1.2sを隠蔽。音質・応答内容への影響なし)
            preemptive_generation=True,
            # thinkingは遅延の主因なので最小化する。⚠Gemini 3系は thinking_budget=0 が
            # 「無視される」(警告ログのみ、こっそりthinkingが動く) — thinking_level が正
            # (2026-07-18 実機ログの警告で判明。LLM初トークン0.8〜1.2sの一因だった)
            # 🚨**3.6には戻さない** (2026-08-01夜に切り戻し)。同日昼に3.6へ上げたが、
            # **ツール宣言があるとき、関数呼び出しが本文に溢れる**のを実測した:
            #   「…失礼します。幕頭[call:default_api:end_call{}]」「…失礼します。 चिरend_call{}」
            #   「…失礼します。#CALL:default_api:end_call{}」「…失礼します。大吉」
            # ⚠**しかもその回はツールが呼ばれていない** = 切るつもりで切れていない。
            #   「end_call が実測4/5」と記録してきた現象の正体はこれの可能性が高い。
            # ⚠この文字列は**TTSに渡るので相手に聞こえている**。記録の汚れでは済まない。
            # 切り分け (infra/scripts/probe_stray_tokens.py、各15サンプル×3回):
            #   本番構成(3.6+ツール+本番プロンプト) 13/45 異常 / 3.5へ戻す **0/45** /
            #   ツール宣言を外す **0/45** / プロンプトを最小化 1/45 / 温度は無関係(0.2でも同率)
            #   → 3.6 × ツール宣言 の組み合わせが原因。プロンプトの長さは発生率を上げるだけ
            # 再挑戦するなら、上のスクリプトを回して 0/45 になってからにすること
            # ⚠テキストモデルも環境変数で差し替えられるようにした (2026-09-18)。
            #   API の models 一覧には 3.6 / 3.7 / 3.8-flash があるが、**既定は 3.5 のまま**。
            #   8/1 に 3.6 へ上げたとき、ツール宣言があると関数呼び出しが本文に溢れて
            #   相手に聞こえた (13/45)。上げるなら run_scenarios の no_tool_leak で測ってから。
            llm=google.LLM(
                model=os.environ.get("GEMINI_TEXT_MODEL", "gemini-3.5-flash"),
                temperature=0.7,
                thinking_config=genai_types.ThinkingConfig(thinking_level="low"),
            ),
            tts=_make_tts(tts_primary, tts_fallback),
        )
    return AgentSession(
        user_away_timeout=SILENCE_SEC,
        # ⚠**live でも手元の VAD が要る** (2026-09-18、ユーザーが実機で「自分で話していても
        #   『もしもし？』になる」と指摘して判明)。Gemini のプラグインは相手の発話開始を
        #   **割り込まれたときしか**フレームワークに伝えない (realtime_api.py の
        #   _handle_input_speech_started は interrupted と自発 generate の直前だけ)。
        #   だから user_state は一度も speaking にならず、AI が黙った SILENCE_SEC 秒後に
        #   相手が喋っていようが away → 「もしもし？聞こえていますか？」が必ず出ていた。
        #   turn ログ (06:24 の aizuchi) に user listening→speaking が 1 件も無いのが証拠。
        #   ターン検出そのものは引き続きサーバー側 (Gemini)。
        # ⚠VAD が効くのは user_state (無言判定・「相手が話している最中か」) だけ (2026-10-02 に確認)。
        #   ターン検出が realtime_llm のとき、フレームワークは VAD による割り込みを飛ばす
        #   (agent_activity.on_vad_inference_done の冒頭)。下の min_interruption_duration も live では効かない。
        # LIVE_VAD=webrtc で軽い VAD に替えられる (webrtc_vad.py。試験中、既定は silero)
        vad=_make_live_vad(),
        # s2sは語数判定に使えるSTT結果を持たないので秒数だけで抑える
        min_interruption_duration=interrupt_sec,
        # ⚠既定を **gemini-3.8-live** にした (2026-09-18)。7/30に「再検討する」と置いた
        #   2条件のうち②が解けたため。実測した capabilities:
        #     3.1-flash-live : mutable_instructions=False / mutable_chat_context=False
        #     3.8-live       : mutable_instructions=True  / mutable_chat_context=True
        #   ⚠**3.1では chat_ctx も更新できなかった**= inject_chat_text で届けている
        #     **耳打ちも後方支援メモも live 構成では一切届いていなかった**。
        #     (案件mdは「指示更新ができない」としか書いておらず、被害範囲を過小に記録していた)
        #   プラグイン自身も3.1に対して「will not be applied until the next session」と警告する。
        # ⚠モデルIDは実在を API の models 一覧で確認したもの。もう1つ
        #   `gemini-3.8-live-extended-thinking` があり capabilities は同じ。
        #   思考を入れると遅延が増えるはずなので、電話では既定にしない。
        llm=google.beta.realtime.RealtimeModel(
            model=os.environ.get("GEMINI_LIVE_MODEL", "gemini-3.8-live"),
            # 請求先 (2026-10-08、vertex.py)。VERTEX_PROJECT があれば Vertex AI (VM のサービスアカウントで認証)、
            # 無ければ GOOGLE_API_KEY。⚠Live は us-central1 にしか無い
            **(
                {"vertexai": True, "project": vertex.project(), "location": vertex.live_location()}
                if vertex.project()
                else {}
            ),
            voice=os.environ.get("GEMINI_VOICE", "Leda"),
            language="ja-JP",
            # ⚠相手の声の文字起こしも日本語に固定する (2026-09-29)。既定は言語の自動判定で、
            #   関西弁の年配の相手 (ユーザーの父) の声がスペイン語・ベンガル語・中国語として
            #   記録され、AI もスペイン語で返した。language= は AI の声の言語で、こちらには効かない
            input_audio_transcription=genai_types.AudioTranscriptionConfig(language_codes=["ja-JP"]),
            # ⚠長電話の費用に上限を付ける (2026-10-02)。Live API は**毎ターン、それまでの文脈を全部
            #   入力として数え直して課金する**ので、費用は通話の長さの 2 乗で増える (2分の試験通話で
            #   音声入力 398→938→1,678 と累積)。文脈が LIVE_CONTEXT_TRIGGER (既定 64,000 ≒ 会話20分)
            #   を超えたら古いやり取りから詰める。普通の通話 (数分) はここに届かないので何も変わらない
            context_window_compression=genai_types.ContextWindowCompressionConfig(
                trigger_tokens=int(os.environ.get("LIVE_CONTEXT_TRIGGER") or "64000"),
                sliding_window=genai_types.SlidingWindow(
                    target_tokens=int(os.environ.get("LIVE_CONTEXT_TARGET") or "32000")
                ),
            ),
            temperature=0.8,
            # ⚠相手の「間」への食い付きを鈍らせる (2026-09-18)。
            #   3.8 は既定だと相手が「佐藤」で一拍置いた瞬間に返事を始め、相手の続きが
            #   拾われないまま無言扱いになった (実測 05:50:41、aizuchi の no_truncated_ai も同型)。
            #   電話は相槌と言い淀みが多いので、発話終了の判定を鈍く・沈黙の要求時間を長めにする。
            #   ⚠上げすぎると返事が遅れる (7/16「電話は遅延が先に体験を壊す」)。
            #     LIVE_END_SILENCE_MS で調整し、run_scenarios の aizuchi と ttft で両側を見る
            realtime_input_config=genai_types.RealtimeInputConfig(
                automatic_activity_detection=genai_types.AutomaticActivityDetection(
                    end_of_speech_sensitivity=genai_types.EndSensitivity.END_SENSITIVITY_LOW,
                    silence_duration_ms=int(os.environ.get("LIVE_END_SILENCE_MS", "800")),
                )
            ),
        ),
        # say() 用のTTS (Live APIと同系のLedaの声)。⚠**3.8でも supports_say=False のまま**なので、
        # 固定挨拶は引き続き say() で確定再生する (2026-07-17実機エラーで判明・2026-09-18に再確認)
        # ⚠管制室の声の設定 (tts_primary / tts_fallback) は **live では見ない** (2026-09-19)。
        #   ここで作る TTS は挨拶・無言時の促し・切る前の一言の 3 つにしか使われず、会話本体は
        #   Gemini の GEMINI_VOICE で喋る。設定で Aivis 等を選ぶと定型文だけ別人の声になるので、
        #   Live API と同系の Google Cloud TTS (GOOGLE_TTS_VOICE = Chirp3-HD の同名の声) に固定する。
        #   管制室の設定ページも live のときは声の選択を出さない (settings/page.tsx)
        tts=_make_tts("google", "gemini"),
    )
