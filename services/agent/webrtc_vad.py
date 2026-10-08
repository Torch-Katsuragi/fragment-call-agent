# -*- coding: utf-8 -*-
"""軽い VAD (WebRTC の VAD) を livekit-agents の VAD として差し込む (2026-10-02、試験中)。

なぜ:
  live 構成の VAD は「相手がいま話しているか」(user_state) と割り込み判定にだけ使う
  (ターン検出は Gemini のサーバー側)。silero はニューラルネットで、同時通話の負荷テストで
  「VAD inference is slower than realtime」が真っ先に出た。WebRTC の VAD は GMM で、
  同じ 97 秒の通話音声で silero 16kHz 1.6 秒 → 0.03 秒 (手元の PC、約 60 分の 1)。
  ⚠Gemini の入力文字起こしでは代わりにならない: 相手が話している間は 1 件も届かず、
    話し終えて 0.6〜1.0 秒後にまとめて来る (2026-10-02 手元で実測、11 秒と 14.5 秒の発話)

形: silero と同じイベント (START_OF_SPEECH / INFERENCE_DONE / END_OF_SPEECH) を出す。
  END_OF_SPEECH の frames は cascade の STT 用で、live では使わないが互換のために入れる。
"""

import asyncio
import time
from collections import deque

import webrtcvad
from livekit import rtc
from livekit.agents import utils, vad

FRAME_MS = 30  # WebRTC VAD が受け付けるのは 10/20/30ms
_RATES = (8000, 16000, 32000, 48000)


class WebRTCVAD(vad.VAD):
    def __init__(
        self,
        *,
        aggressiveness: int = 2,
        min_speech_duration: float = 0.09,
        min_silence_duration: float = 0.55,
        max_buffered_speech: float = 60.0,
    ) -> None:
        super().__init__(capabilities=vad.VADCapabilities(update_interval=FRAME_MS / 1000))
        self.aggressiveness = aggressiveness
        self.min_speech = min_speech_duration
        self.min_silence = min_silence_duration
        self.max_buffered = max_buffered_speech

    @property
    def model(self) -> str:
        return "webrtcvad"

    @property
    def provider(self) -> str:
        return "webrtc"

    def stream(self) -> "WebRTCVADStream":
        return WebRTCVADStream(self)


class WebRTCVADStream(vad.VADStream):
    def __init__(self, v: WebRTCVAD) -> None:
        super().__init__(v)
        self._v = v

    @utils.log_exceptions()
    async def _main_task(self) -> None:
        det = webrtcvad.Vad(self._v.aggressiveness)
        resampler: rtc.AudioResampler | None = None
        rate = 0
        buf = bytearray()
        frame_bytes = 0
        win = FRAME_MS / 1000

        speaking = False
        speech_dur = 0.0  # 話している区間の長さ (START 後)
        silence_dur = 0.0
        run_speech = 0.0  # 連続して「声あり」の長さ (START 判定用)
        run_silence = 0.0  # 連続して「声なし」の長さ (END 判定用)
        ts = 0.0
        # ⚠長さは足し上げて持つ。毎フレーム sum() すると長い発話で O(n²) になり、イベントループを
        #   塞いで同じプロセスの他の通話の接続まで落とした (2026-10-02 手元で FFI panic)
        speech_frames: deque[rtc.AudioFrame] = deque()
        buffered = 0.0

        async for item in self._input_ch:
            if isinstance(item, self._FlushSentinel):
                buf.clear()
                speaking, speech_dur, silence_dur, run_speech, run_silence = False, 0.0, 0.0, 0.0, 0.0
                speech_frames.clear()
                buffered = 0.0
                continue
            if not isinstance(item, rtc.AudioFrame):
                continue
            if not rate:
                rate = item.sample_rate if item.sample_rate in _RATES else 16000
                if rate != item.sample_rate:
                    resampler = rtc.AudioResampler(
                        input_rate=item.sample_rate,
                        output_rate=rate,
                        quality=rtc.AudioResamplerQuality.QUICK,
                    )
                frame_bytes = int(rate * win) * 2
            frames = resampler.push(item) if resampler else [item]
            for f in frames:
                if f.num_channels != 1:
                    continue
                buf.extend(f.data.tobytes())
            if speaking or run_speech > 0:
                speech_frames.append(item)
                buffered += item.duration
                while buffered > self._v.max_buffered and speech_frames:
                    buffered -= speech_frames.popleft().duration

            while len(buf) >= frame_bytes:
                chunk = bytes(buf[:frame_bytes])
                del buf[:frame_bytes]
                t0 = time.perf_counter()
                is_speech = det.is_speech(chunk, rate)
                infer = time.perf_counter() - t0
                ts += win
                if is_speech:
                    run_speech += win
                    run_silence = 0.0
                else:
                    run_silence += win
                    run_speech = 0.0 if not speaking else run_speech
                if speaking:
                    speech_dur += win
                    silence_dur = run_silence
                else:
                    silence_dur += win

                self._event_ch.send_nowait(
                    vad.VADEvent(
                        type=vad.VADEventType.INFERENCE_DONE,
                        samples_index=int(ts * rate),
                        timestamp=ts,
                        speech_duration=speech_dur,
                        silence_duration=silence_dur,
                        probability=1.0 if is_speech else 0.0,
                        inference_duration=infer,
                        speaking=speaking,
                        raw_accumulated_silence=run_silence,
                        raw_accumulated_speech=run_speech,
                    )
                )
                if not speaking and run_speech >= self._v.min_speech:
                    speaking = True
                    speech_dur = run_speech
                    silence_dur = 0.0
                    self._event_ch.send_nowait(
                        vad.VADEvent(
                            type=vad.VADEventType.START_OF_SPEECH,
                            samples_index=int(ts * rate),
                            timestamp=ts,
                            speech_duration=speech_dur,
                            silence_duration=0.0,
                        )
                    )
                elif speaking and run_silence >= self._v.min_silence:
                    speaking = False
                    self._event_ch.send_nowait(
                        vad.VADEvent(
                            type=vad.VADEventType.END_OF_SPEECH,
                            samples_index=int(ts * rate),
                            timestamp=ts,
                            speech_duration=speech_dur,
                            silence_duration=run_silence,
                            frames=list(speech_frames),
                        )
                    )
                    speech_frames.clear()
                    buffered = 0.0
                    speech_dur = 0.0
                    run_speech = 0.0
            await asyncio.sleep(0)
