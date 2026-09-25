"use client";

import { RoomAudioRenderer } from "@livekit/components-react";

import { useMonitorVolume } from "@/lib/monitorVolume";

// 通話音声の再生。
//
// ⚠**Web Audioで増幅する実装を入れて、外した** (2026-07-31)。
//   <audio> の volume は1.0が上限なので、1.0超に増幅するには Web Audio が要る。
//   そこで track.attach() が作った要素を createMediaElementSource に通したが、
//   実機で**3つ同時に壊れた**:
//     ・音量スライダーを動かしても音が変わらない
//     ・AIの声が途切れる
//     ・自分の声が聞こえない (文字起こしには出ているのに)
//   原因は API の選択ミス。WebRTC のリモートトラックは srcObject 経由なので、
//   **createMediaElementSource ではなく createMediaStreamSource** を使う必要がある
//   (MediaStream を直接ソースにする。加えて Chrome ではトラックを流し続けるために
//    ミュートした <audio> に attach したままにする小細工も要る)。
//   電話系で音が壊れるのが一番まずいので、確実に動く RoomAudioRenderer に戻した。
//
//   ⚠したがって**いまは100%を超えられない**。増幅が本当に必要なら、上記の正しいAPIで
//   組み直して**実回線で確かめてから**入れること。机上で直して入れ直すと、また同じ壊し方をする。
// muted: 通話画面の外で、かつ「流す」設定がオフのとき (CallSession.tsx が決める)。
// ⚠再生要素そのものは残す — 外すと LiveKit がトラックを流さなくなり、戻したとき音が出るまで遅れる
export default function MonitorAudio({ muted = false }: { muted?: boolean }) {
  const [volume] = useMonitorVolume();
  return <RoomAudioRenderer volume={Math.min(1, volume)} muted={muted} />;
}
