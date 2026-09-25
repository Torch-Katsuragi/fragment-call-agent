# -*- coding: utf-8 -*-
"""operator_sim.py — 本人役シミュレータ (テスト自動化用、2026-07-19)。

管制室の「🎙自分が出る」を模して、ブラウザを介さず LiveKit SDK で直接ルームに
`operator-sim<乱数>` 識別子で参加する。あらかじめ用意したセリフを Cloud TTS で
順に合成・発話し、指定秒数後に自動退室する (handback検証)。

内蔵ブラウザ (Browser pane) はマイク権限がブロックされ、operator接続が数秒で
不安定に切れる実測があった (2026-07-19)。本スクリプトはSDK直結で音声トラックを
公開するため、ブラウザに依存せず安定して operator 側の発話を再現できる。

識別子が "operator-" で始まる点だけが agent.py の交代検知 (_handover/handback) と
caller_sim.py の聴く相手切替の判定条件なので、本スクリプトの参加/退室だけで
両方が本物のダッシュボード操作と同じに動く (agent.py/caller_sim.py の改修不要)。

使い方 (agentコンテナ内、compose execで実行):
  python operator_sim.py <room_name> "セリフ1" "セリフ2" ... [--leave-after 秒]

例 (前回の件を持ち出しつつ折り返し番号を確認するテスト):
  python operator_sim.py call_09012345678_simXXXXXX \
    "はい、〇〇です" \
    "折り返しはこの番号じゃなくて携帯の090-9999-8888にかけてください" \
    --leave-after 3
"""

import argparse
import array
import asyncio
import io
import logging
import os
import wave

from google.cloud import texttospeech as gcloud_tts
from livekit import api as lk_api
from livekit import rtc

logger = logging.getLogger("operator-sim")

FRAME_MS = 20  # LiveKit公式サンプルに合わせた1フレームの長さ


def _synth_pcm(client: gcloud_tts.TextToSpeechClient, text: str, voice: str) -> tuple[bytes, int]:
    """Cloud TTSでLINEAR16(WAV)合成→生PCM+サンプルレートに剥く。"""
    resp = client.synthesize_speech(
        input=gcloud_tts.SynthesisInput(text=text),
        voice=gcloud_tts.VoiceSelectionParams(language_code="ja-JP", name=voice),
        # sample_rate_hertz を明示しないと声種のネイティブレート (Chirp3-HDは24000Hz) が返り、
        # AudioSource(16000, 1) 固定と不一致で "sample_rate and num_channels don't match" になる
        audio_config=gcloud_tts.AudioConfig(
            audio_encoding=gcloud_tts.AudioEncoding.LINEAR16, sample_rate_hertz=16000
        ),
    )
    with wave.open(io.BytesIO(resp.audio_content), "rb") as w:
        assert w.getsampwidth() == 2, "16bit PCM前提"
        pcm = w.readframes(w.getnframes())
        return pcm, w.getframerate()


async def _publish_line(source: rtc.AudioSource, pcm: bytes, sample_rate: int) -> None:
    """PCMを20msフレームに刻み、実時間ペースでcapture_frameへ流し込む。"""
    bytes_per_frame = int(sample_rate * FRAME_MS / 1000) * 2  # 16bit mono
    for i in range(0, len(pcm), bytes_per_frame):
        chunk = pcm[i : i + bytes_per_frame]
        if len(chunk) < bytes_per_frame:
            chunk += b"\x00" * (bytes_per_frame - len(chunk))  # 末尾フレームを無音でパディング
        frame = rtc.AudioFrame.create(sample_rate, 1, len(chunk) // 2)
        samples = array.array("h")  # AudioFrame.data は int16 memoryview (format 'h')
        samples.frombytes(chunk)
        frame.data[:] = samples
        await source.capture_frame(frame)
        await asyncio.sleep(FRAME_MS / 1000)


async def run(room_name: str, lines: list[str], leave_after: float, gap: float, rejoin_after: float) -> None:
    url = os.environ.get("LIVEKIT_URL", "ws://livekit:7880")
    api_key = os.environ["LIVEKIT_API_KEY"]
    api_secret = os.environ["LIVEKIT_API_SECRET"]
    creds = os.environ.get("GOOGLE_TTS_CREDENTIALS", "/secrets/callagent-tts.json")
    voice = os.environ.get("OPERATOR_SIM_VOICE", "ja-JP-Chirp3-HD-Leda")

    identity = f"operator-sim{os.getpid()}"
    token = (
        lk_api.AccessToken(api_key, api_secret)
        .with_identity(identity)
        .with_name("operator-sim")
        .with_grants(
            lk_api.VideoGrants(
                room_join=True, room=room_name, can_publish=True, can_subscribe=True,
                can_publish_data=False,
            )
        )
        .to_jwt()
    )

    tts_client = gcloud_tts.TextToSpeechClient.from_service_account_file(creds)
    room = rtc.Room()
    await room.connect(url, token)
    logger.info("operator-sim joined %s as %s", room_name, identity)

    source = rtc.AudioSource(16000, 1)
    track = rtc.LocalAudioTrack.create_audio_track("operator-sim-voice", source)
    await room.local_participant.publish_track(track, rtc.TrackPublishOptions())

    for i, line in enumerate(lines):
        logger.info("operator-sim speaking [%d/%d]: %s", i + 1, len(lines), line)
        pcm, sr = _synth_pcm(tts_client, line, voice)
        source.clear_queue()
        await _publish_line(source, pcm, sr)
        if i < len(lines) - 1:
            await asyncio.sleep(gap)  # AIの応答を待つ間

    if leave_after > 0:
        logger.info("operator-sim: %.1f秒後に退室 (handback検証)", leave_after)
        await asyncio.sleep(leave_after)
    await room.disconnect()
    logger.info("operator-sim left %s", room_name)

    if rejoin_after > 0:
        logger.info("operator-sim: %.1f秒後に再入室 (handback→再交代の検証)", rejoin_after)
        await asyncio.sleep(rejoin_after)
        room2 = rtc.Room()
        await room2.connect(url, token)
        logger.info("operator-sim rejoined %s", room_name)
        await asyncio.sleep(5)
        await room2.disconnect()
        logger.info("operator-sim left %s (2回目)", room_name)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("room_name")
    p.add_argument("lines", nargs="+", help="順番に発話するセリフ")
    p.add_argument("--gap", type=float, default=5.0, help="セリフ間でAIの応答を待つ秒数")
    p.add_argument("--leave-after", type=float, default=3.0, help="最後のセリフ後、退室までの待ち秒数")
    p.add_argument("--rejoin-after", type=float, default=0.0, help="退室後、再入室までの待ち秒数 (0=再入室しない)")
    args = p.parse_args()
    asyncio.run(run(args.room_name, args.lines, args.leave_after, args.gap, args.rejoin_after))


if __name__ == "__main__":
    main()
