"""Aivis Cloud API 用の livekit-agents カスタムTTSアダプタ (公式プラグインが無いため自作)。

なぜ入れたか (2026-07-30):
  Google Cloud TTS (Chirp3-HD) は品質は良いが「機械感が強くて話しづらい」と実機で言われた。
  子供っぽい声で応対すると**機械っぽさが「そういうもの」として許される**という読みで、
  AivisSpeech の声 (既定はコハク) に載せ替える。

実測 (2026-07-30、VMから):
  - 初回バイト **0.08〜0.12秒**。速度指定に関係なく安定 (公称「最速0.3秒」より速い)
  - 35文字の合成で総時間0.46秒
  - コハクは**ゆっくり読む声質**。35文字が rate=1.0 で6.5秒、1.15で5.8秒、1.3で5.3秒。
    23文字の挨拶なら rate=1.2 で3.9秒 → 挨拶を割り込み無効で流す窓に収まる
  - 課金は 1万文字¥440 = **¥0.044/字**。テスト6回(約190字)で8.272クレジット消費と一致

⚠use_ssml は既定 True だが、ここでは **False 固定**。会話文をそのまま渡すので、
  `<` や `&` が混ざるとSSMLとして壊れて無音になりうる。読み上げに記号は要らない。

実装契約は voicevox_tts.py と同じ (livekit-plugins-groq の tts.py 1.6.5 を踏襲):
  ChunkedStream._run(output_emitter) で initialize → push → flush。
  ただし Aivis はレスポンスがストリームで返るので**届いた順に push する** —
  ここが初回バイト0.1秒を活かせる箇所。
"""

from __future__ import annotations

import aiohttp

from livekit.agents import APIConnectionError, APIConnectOptions, APIStatusError, tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

# コハク (猫音コハク ©Oz Chat/Trippy、声優 ねゆたろ)。ライセンスは ACML 1.0 = 商用可・クレジット任意。
# ⚠声を借りるのとキャラクターを演じるのは別。AIに「コハクです」と名乗らせてはいけない
DEFAULT_MODEL_UUID = "22e8ed77-94fe-4ef2-871f-a86f94e9a579"


class AivisTTS(tts.TTS):
    def __init__(
        self,
        *,
        api_key: str,
        model_uuid: str = DEFAULT_MODEL_UUID,
        speaker_uuid: str | None = None,
        style_name: str | None = None,
        speaking_rate: float = 1.2,
        # ⚠既定1.5。1.0では「小さい」、2.0では「うるさい」と実機で両方言われた (2026-07-30/31)。
        #   同じ文の発話RMS実測: Aivis vol=1.0 → 2663 / vol=2.0 → 5133 /
        #   切替前の Google Chirp3-HD → 6665。**Googleが2.5倍(約8dB)大きかった**のが原因。
        #   ⚠**Googleに合わせるという目標設定が誤り**だった。そのGoogle自体が適正か
        #   確かめておらず、実測でも一番大きかった = うるさい基準に揃えていた。
        #   これでも足りなければ次の手はPCMへのソフトゲイン (audioop.mul) を足すこと
        volume: float = 1.5,
        # ⚠**切らない**。各セグメントをRMSで揃える機能で、文は1つずつ別々に合成されるため、
        #   切ると文ごとに音量がばらつく。電話では「少し小さい」より「揃っていない」方が耳障り。
        #   参考: vol=2.0 で正規化を切るとRMS6711まで上がるが**クリップが出る**
        use_volume_normalizer: bool = True,
        emotional_intensity: float = 1.0,
        pitch: float = 0.0,
        sample_rate: int = 24000,
        base_url: str = "https://api.aivis-project.com",
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        if not api_key:
            raise ValueError("AIVIS_API_KEY が未設定です")
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._api_key = api_key
        self._model_uuid = model_uuid
        self._speaker_uuid = speaker_uuid
        self._style_name = style_name
        self._speaking_rate = speaking_rate
        self._volume = max(0.0, min(2.0, volume))  # APIの許容は 0.0〜2.0 (超えると422)
        self._use_volume_normalizer = use_volume_normalizer
        self._emotional_intensity = emotional_intensity
        self._pitch = pitch
        self._base_url = base_url.rstrip("/")
        self._session = http_session

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None:
            self._session = utils.http_context.http_session()
        return self._session

    def synthesize(
        self,
        text: str,
        *,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> ChunkedStream:
        return ChunkedStream(tts=self, input_text=text, conn_options=conn_options)


class ChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: AivisTTS, input_text: str, conn_options: APIConnectOptions):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._aivis: AivisTTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        a = self._aivis
        session = a._ensure_session()
        body: dict = {
            "model_uuid": a._model_uuid,
            "text": self._input_text,
            "use_ssml": False,  # ⚠上の注記参照。会話文をSSMLとして解釈させない
            "output_format": "wav",
            "output_sampling_rate": a.sample_rate,
            "output_audio_channels": "mono",
            "speaking_rate": a._speaking_rate,
            "volume": a._volume,
            "use_volume_normalizer": a._use_volume_normalizer,
            # 電話では前後の無音がそのまま「間」になるので削る (既定は各0.1秒)
            "leading_silence_seconds": 0.0,
            "trailing_silence_seconds": 0.0,
        }
        # ⚠既定値のまま送らない方がよい2つ (APIドキュメントの注意書き):
        #   pitch を0.0から変えると**音質が劣化し生成速度も大幅に低下する**
        #   emotional_intensity は**ノーマルスタイルでは効果がない**
        if a._pitch:
            body["pitch"] = a._pitch
        if a._emotional_intensity != 1.0:
            body["emotional_intensity"] = a._emotional_intensity
        if a._speaker_uuid:
            body["speaker_uuid"] = a._speaker_uuid
        if a._style_name:
            body["style_name"] = a._style_name

        try:
            async with session.post(
                f"{a._base_url}/v1/tts/synthesize",
                headers={"Authorization": f"Bearer {a._api_key}"},
                json=body,
            ) as res:
                if res.status != 200:
                    # ⚠本文にAPIキーは載らないが、念のため先頭200文字までに切る
                    detail = (await res.text())[:200]
                    raise APIStatusError(
                        f"Aivis TTS が {res.status} を返しました: {detail}",
                        status_code=res.status,
                    )
                output_emitter.initialize(
                    request_id=utils.shortuuid(),
                    sample_rate=a.sample_rate,
                    num_channels=1,
                    mime_type="audio/wav",
                )
                # 届いた順に流す (初回バイト0.1秒を活かす)
                async for chunk in res.content.iter_chunked(8192):
                    output_emitter.push(chunk)
                output_emitter.flush()
        except aiohttp.ClientError as e:
            raise APIConnectionError(f"Aivis Cloud API ({a._base_url}) に接続できません") from e
