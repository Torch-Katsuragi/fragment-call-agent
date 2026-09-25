"""VOICEVOX 用の livekit-agents カスタムTTSアダプタ (公式プラグインが無いため自作)。

- VOICEVOX ENGINE の HTTP API (audio_query → synthesis) を叩く非ストリーミングTTS。
  livekit-agents が非ストリーミングTTSを自動で StreamAdapter (文単位の分割合成) に
  ラップするので、応答が長くても最初の文からすぐ再生が始まる
- AivisSpeech Engine (VOICEVOX互換API・より自然な声) でも base_url と speaker を
  差し替えるだけでそのまま動く
- 実装契約は livekit-plugins-groq の tts.py (1.6.5) を踏襲:
  ChunkedStream._run(output_emitter) で initialize → push(wavバイト列) → flush
"""

from __future__ import annotations

import aiohttp

from livekit.agents import APIConnectionError, APIConnectOptions, tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS


class VoicevoxTTS(tts.TTS):
    def __init__(
        self,
        *,
        base_url: str = "http://localhost:50021",
        speaker: int = 2,  # 2 = 四国めたん(ノーマル)。一覧は GET {base_url}/speakers
        speed: float = 1.0,
        sample_rate: int = 24000,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=sample_rate,
            num_channels=1,
        )
        self._base_url = base_url.rstrip("/")
        self._speaker = speaker
        self._speed = speed
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
    def __init__(self, *, tts: VoicevoxTTS, input_text: str, conn_options: APIConnectOptions):
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._voicevox: VoicevoxTTS = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        v = self._voicevox
        session = v._ensure_session()
        params = {"text": self._input_text, "speaker": str(v._speaker)}
        try:
            async with session.post(f"{v._base_url}/audio_query", params=params) as res:
                res.raise_for_status()
                query = await res.json()

            query["outputSamplingRate"] = v.sample_rate
            query["outputStereo"] = False
            query["speedScale"] = v._speed

            async with session.post(
                f"{v._base_url}/synthesis",
                params={"speaker": str(v._speaker)},
                json=query,
            ) as res:
                res.raise_for_status()
                wav = await res.read()

            output_emitter.initialize(
                request_id=utils.shortuuid(),
                sample_rate=v.sample_rate,
                num_channels=1,
                mime_type="audio/wav",
            )
            output_emitter.push(wav)
            output_emitter.flush()
        except aiohttp.ClientError as e:
            raise APIConnectionError(f"VOICEVOX engine ({v._base_url}) に接続できません") from e
