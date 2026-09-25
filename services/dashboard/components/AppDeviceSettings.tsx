"use client";

import { useEffect, useState } from "react";
import Toggle from "@/components/Toggle";

// スマホアプリの端末側設定 (2026-09-19)。**アプリの WebView で開いたときだけ**出る。
//
// 値はサーバーではなく端末 (アプリの Prefs) にある。WebView が window.FragmentNative という
// 橋を差し込んでくるので、それがあるときだけ行を描き、読み書きも橋に任せる。
// ⚠「鳴らす/鳴らさない」は**この端末の分だけ**をここと常駐通知で切り替える (2026-09-24)。
//   管制室から他の端末を切り替える口は撤去した (ユーザー「事故のもと」)。値の正は端末で、
//   サーバーへは一方向に知らせるだけ (DevicePause.sync → /api/device/pause)

declare global {
  interface Window {
    FragmentNative?: {
      getLiveDisplay(): string;
      setLiveDisplay(mode: string): void;
      getPaused?(): boolean;
      setPaused?(paused: boolean): void;
    };
  }
}

const OPTIONS = [
  { key: "none", label: "なし" },
  { key: "visualizer", label: "名前と波形" },
  { key: "chat", label: "会話" },
] as const;

export default function AppDeviceSettings() {
  const [bridge, setBridge] = useState<Window["FragmentNative"] | null>(null);
  const [mode, setMode] = useState("none");
  const [paused, setPaused] = useState<boolean | null>(null);

  useEffect(() => {
    const b = window.FragmentNative;
    if (!b) return;
    setBridge(b);
    try {
      setMode(b.getLiveDisplay() || "none");
    } catch {
      /* 古いアプリ */
    }
    try {
      if (b.getPaused) setPaused(!!b.getPaused());
    } catch {
      /* 古いアプリ */
    }
  }, []);

  if (!bridge) return null;

  return (
    <>
    {paused !== null && (
      <div className="set-row inline">
        <div className="set-row-text">
          <div className="set-row-title">この端末を鳴らす</div>
          <div className="set-row-desc">{paused ? "鳴りません (AIの応対はそのまま)" : "着信と取り次ぎで鳴ります"}</div>
        </div>
        <Toggle
          on={!paused}
          onChange={(on) => {
            setPaused(!on);
            try {
              bridge.setPaused?.(!on);
            } catch {
              /* 古いアプリ */
            }
          }}
          label="この端末を鳴らす"
        />
      </div>
    )}
    <div className="set-row">
      <div className="set-row-text">
        <div className="set-row-title">AI応対中のロック画面</div>
        <div className="set-row-desc">ロック中だけ表示し、通話が終わると閉じます</div>
      </div>
      <div className="segmented" role="group" aria-label="AI応対中のロック画面表示">
        {OPTIONS.map((o) => (
          <button
            key={o.key}
            className={`segmented-btn ${o.key === mode ? "on" : ""}`}
            aria-pressed={o.key === mode}
            onClick={() => {
              setMode(o.key);
              try {
                bridge.setLiveDisplay(o.key);
              } catch {
                /* 古いアプリ */
              }
            }}
          >
            {o.label}
          </button>
        ))}
      </div>
    </div>
    </>
  );
}
