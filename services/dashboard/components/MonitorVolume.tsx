"use client";

import {
  MAX_VOLUME,
  MIN_VOLUME,
  useMonitorVolume,
} from "@/lib/monitorVolume";

// モニタ音量のスライダー。**設定画面と通話画面の両方から同じ値を触る**ので、
// どちらで変えても即座に揃う (localStorage + カスタムイベント → lib/monitorVolume.ts)。
// compact=true は通話画面のバー用 (説明文なしの細い表示)。
export default function MonitorVolume({ compact = false }: { compact?: boolean }) {
  const [volume, setVolume] = useMonitorVolume();
  const pct = Math.round(volume * 100);

  const slider = (
    <input
      type="range"
      min={MIN_VOLUME}
      max={MAX_VOLUME}
      step={0.1}
      value={volume}
      onChange={(e) => setVolume(Number(e.target.value))}
      title={`モニタ音量 ${pct}%`}
      aria-label="モニタ音量"
    />
  );

  if (compact) {
    return (
      <label className="devsel" title="モニタ音量 (こちらが聞く音量。相手には影響しません)">
        🔉
        {slider}
        <span className="muted">{pct}%</span>
      </label>
    );
  }

  // 設定ページ版。値はこの端末 (ブラウザ) にだけ保存 (スピーカーで適正音量が違うため)
  return (
    <div className="set-row inline">
      <div className="set-row-text">
        <div className="set-row-title">モニタ音量</div>
        <div className="set-row-desc">この端末で聞く音量 (相手には影響しません)</div>
      </div>
      <div className="slider-ctl">
        {slider}
        <span className="set-val-inline">{pct}%</span>
      </div>
    </div>
  );
}
