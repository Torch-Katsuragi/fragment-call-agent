# -*- coding: utf-8 -*-
"""保留音 (2026-09-26)。保留中 (会話中の端末が無く AI 応答もオフ) に agent がループで流す。

呼び出し音 (render_ringback.py) とは別の曲にする。呼び出し音は「まだ誰も出ていない」、
保留音は「出た人が待たせている」で、意味が違うので。
呼び出し音より遅く低く、柔らかい音色 (エレピ風) で、切れ目なく回る 4 小節。
⚠既成の保留音 (「グリーンスリーブス」の録音等) は使わない。これは自作。
⚠agent (LiveKit) が流すので 16kHz で作る (電話に出るときは 8kHz に落ちる)。

  python infra/scripts/render_hold.py   # → services/agent/assets/hold.wav (1周 約 9.6 秒)
"""
import array
import math
import wave
from pathlib import Path

SR = 16000
BEAT = 0.6  # 1音の間隔 (秒)
# ヘ長調のペンタトニックで、上って・揺れて・主音に戻る
MELODY = "F4 A4 C5 A4  G4 C5 D5 C5  A4 C5 F5 D5  C5 A4 G4 F4".split()
BASS = "F3 C3 D3 C3".split()  # 1小節に1つ
FREQ = {
    "C3": 130.81, "D3": 146.83, "F3": 174.61,
    "F4": 349.23, "G4": 392.00, "A4": 440.00,
    "C5": 523.25, "D5": 587.33, "F5": 698.46,
}


def tone(freq: float, dur: float, decay: float) -> list[float]:
    """エレピ風: 立ち上がり 10ms・ゆっくり減衰・2倍音を少し"""
    out = []
    for i in range(int(SR * dur)):
        t = i / SR
        env = math.exp(-t * decay) * min(1.0, t / 0.01)
        v = math.sin(2 * math.pi * freq * t) + 0.3 * math.sin(2 * math.pi * freq * 2 * t) * math.exp(-t * 4)
        out.append(env * v)
    return out


def main() -> None:
    length = len(MELODY) * BEAT
    buf = [0.0] * int(SR * length)

    def add(samples: list[float], start: float, gain: float) -> None:
        s = int(SR * start)
        for i, v in enumerate(samples):
            # 終わりからはみ出した余韻は頭に回す (ループのつなぎ目で音が切れないように)
            buf[(s + i) % len(buf)] += gain * v

    for k, name in enumerate(MELODY):
        add(tone(FREQ[name], BEAT * 2.5, 2.2), k * BEAT, 0.42)
    for k, name in enumerate(BASS):
        add(tone(FREQ[name], BEAT * 4, 0.9), k * BEAT * 4, 0.28)

    peak = max(abs(v) for v in buf) or 1.0
    pcm = array.array("h", [int(v / peak * 18000) for v in buf])
    out = Path(__file__).resolve().parents[2] / "services" / "agent" / "assets" / "hold.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())
    print(out, f"{length:.1f}s")


if __name__ == "__main__":
    main()
