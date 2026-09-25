# -*- coding: utf-8 -*-
"""Asterisk が流す定型文の音声を作る (2026-09-25)。

電話を取った直後の「この通話は録音されます」は、AI の TTS ではなくダイヤルプランが
この音声ファイルを流す。TTS の待ちが無く、相手の声で遮られることもない。

声は agent の Google TTS と同じ (GOOGLE_TTS_VOICE、既定 ja-JP-Chirp3-HD-Leda)。
文言や声を変えたらこれを流し直してコミットする。認証は Cloud TTS のサービスアカウント鍵なので
agent コンテナの中で動かす (VM 上で):

  docker cp render_prompts.py infra-agent-1:/tmp/r.py
  docker exec infra-agent-1 python /tmp/r.py > infra/asterisk/sounds/rec-notice.wav
"""
import os
import sys

from google.cloud import texttospeech

TEXT = "この通話は録音されます。"

client = texttospeech.TextToSpeechClient.from_service_account_file(
    os.environ.get("GOOGLE_TTS_CREDENTIALS", "/secrets/callagent-tts.json")
)
res = client.synthesize_speech(
    input=texttospeech.SynthesisInput(text=TEXT),
    voice=texttospeech.VoiceSelectionParams(
        language_code="ja-JP", name=os.environ.get("GOOGLE_TTS_VOICE", "ja-JP-Chirp3-HD-Leda")
    ),
    # ⚠Asterisk の .wav は 8kHz モノラル 16bit しか素直に読まない (電話の帯域もこれ)
    audio_config=texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16, sample_rate_hertz=8000
    ),
)
sys.stdout.buffer.write(res.audio_content)  # LINEAR16 は WAV ヘッダ付きで返る
