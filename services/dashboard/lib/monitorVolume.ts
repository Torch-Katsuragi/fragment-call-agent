"use client";

import { useEffect, useState } from "react";

// 管制室のモニタ音量 (2026-07-31)。
//
// ⚠これは**こちらが聞く音量**で、相手に届く声の音量 (AIVIS_VOLUME) とは別物。
//   混同すると「うるさい」と言われて相手側を下げてしまう、といった取り違えが起きる。
//
// ⚠なぜ1.0を超えられる必要があるか: <audio> 要素の volume は**1.0が上限**なので、
//   「元の音より大きく」はできない。電話音声は8kHzの狭帯域で正規化もされておらず、
//   ラウドネス正規化された音楽 (YouTube Music等) より本質的に小さい。
//   Web Audio の GainNode を挟めば1.0超に増幅できる (components/MonitorAudio.tsx)。
//
// ⚠保存先が localStorage = **ブラウザごと**なのは意図的。適正音量はスピーカーや
//   ヘッドホンに依存するので、PCで合わせた値をスマホに配ると却って合わない。
//   サーバ側 (settingsテーブル) に置いて端末間で揃えたくなったらそのとき移す。
const KEY = "fragment.monitorVolume";
const EVENT = "fragment:monitorVolume";

export const MIN_VOLUME = 0.2;
// ⚠上限は**1.0 (100%)**。<audio> の volume がそこで頭打ちだから。
//   1.0超の増幅には Web Audio が要るが、実装して実機で壊したので外した
//   (経緯は components/MonitorAudio.tsx の注記)。増幅を入れ直すまでは減衰専用。
export const MAX_VOLUME = 1.0;
export const DEFAULT_VOLUME = 1.0;

export function readMonitorVolume(): number {
  if (typeof window === "undefined") return DEFAULT_VOLUME;
  const raw = window.localStorage.getItem(KEY);
  const v = raw === null ? NaN : Number(raw);
  if (!Number.isFinite(v)) return DEFAULT_VOLUME;
  return Math.min(MAX_VOLUME, Math.max(MIN_VOLUME, v));
}

export function writeMonitorVolume(v: number) {
  const clamped = Math.min(MAX_VOLUME, Math.max(MIN_VOLUME, v));
  window.localStorage.setItem(KEY, String(clamped));
  // 同じタブ内の他コンポーネントにも伝える (storageイベントは他タブにしか飛ばない)
  window.dispatchEvent(new CustomEvent(EVENT, { detail: clamped }));
}

/** 設定画面と通話画面の両方から使う。どちらで変えても即座に揃う。 */
export function useMonitorVolume(): [number, (v: number) => void] {
  const [volume, setVolume] = useState(DEFAULT_VOLUME);

  useEffect(() => {
    setVolume(readMonitorVolume()); // 初回はSSRとの差異を避けるため effect で読む
    const onCustom = (e: Event) => setVolume((e as CustomEvent<number>).detail);
    const onStorage = (e: StorageEvent) => {
      if (e.key === KEY) setVolume(readMonitorVolume());
    };
    window.addEventListener(EVENT, onCustom);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(EVENT, onCustom);
      window.removeEventListener("storage", onStorage);
    };
  }, []);

  return [volume, writeMonitorVolume];
}
