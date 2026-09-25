# -*- coding: utf-8 -*-
"""相手に聞かせる呼び出し音 (2026-09-25)。録音告知の後、本人かAIが出るまでループで流す。

日本の標準の呼び出し音 (400Hz・1秒鳴って2秒休み) の代わりに、12音で一まとまりの
オリジナルの短い旋律にした。単調な音より待ってもらいやすい、という判断
(聞き比べて A=ペンタトニックで上って下りる案に決定)。
⚠既成の呼び出し音 (LINE 等) やメロディは各社の著作物なので使わない。これは自作。
⚠電話の帯域に合わせて 8kHz モノラルで作る (Asterisk の .wav もこれしか素直に読まない)。

  python infra/scripts/render_ringback.py   # → infra/asterisk/moh/ringback.wav (1回分、約3.4秒)
"""
import array
import math
import wave
from pathlib import Path

SR = 8000
STEP = 0.16  # 1音の間隔 (秒)
TAIL = 0.5  # 最後の音の余韻
REST = 1.0  # 1回ごとの休み
NOTES = "C5 E5 G5 C6 E6 C6 G5 E5 D5 G5 C6 G5".split()
FREQ = {"C5": 523.25, "D5": 587.33, "E5": 659.25, "G5": 783.99, "C6": 1046.5, "E6": 1318.5}


def note(freq: float, dur: float) -> list[float]:
    """マリンバ風: 立ち上がり5ms・速い減衰・4倍音を少し"""
    out = []
    for i in range(int(SR * dur)):
        t = i / SR
        env = math.exp(-t * 6.0) * min(1.0, t / 0.005)
        v = math.sin(2 * math.pi * freq * t) + 0.25 * math.sin(2 * math.pi * freq * 4 * t) * math.exp(-t * 20)
        out.append(0.5 * env * v)
    return out


def main() -> None:
    buf = [0.0] * int(SR * (len(NOTES) * STEP + TAIL + REST))
    for k, name in enumerate(NOTES):
        start = int(SR * k * STEP)
        for i, v in enumerate(note(FREQ[name], STEP + TAIL)):
            if start + i < len(buf):
                buf[start + i] += v
    pcm = array.array("h", [max(-32767, min(32767, int(v * 20000))) for v in buf])
    out = Path(__file__).resolve().parents[1] / "asterisk" / "moh" / "ringback.wav"
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    print(out, f"{len(buf) / SR:.1f}s")


if __name__ == "__main__":
    main()
