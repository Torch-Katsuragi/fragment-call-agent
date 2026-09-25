"use client";

import Toggle from "@/components/Toggle";
import { useMonitorOutsideCall } from "@/lib/monitorPolicy";

// 「通話画面の外でも音声を流す」(2026-09-18)。値はこの端末 (ブラウザ) にだけ保存。
// 通話中チップ側のボタンは CallSession.tsx にある (同じ値を触る)。
export default function MonitorOutsideCallToggle() {
  const [on, setOn] = useMonitorOutsideCall();
  return (
    <div className="set-row inline">
      <div className="set-row-text">
        <div className="set-row-title">通話画面の外でも音を流す</div>
        <div className="set-row-desc">オフなら通話画面を開いたときだけ流します</div>
      </div>
      <Toggle on={on} onChange={setOn} label="通話画面の外でも音を流す" />
    </div>
  );
}
