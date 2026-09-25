"use client";

import { ANSWER_MODE_UI, ModeButtons, useAnswerMode } from "./AnswerModeControl";

// 設定ページの応答モード行。スイッチ本体はトップと共通の ModeButtons
// (⚠トップと設定で別物にすると、片方だけ2値のまま取り残される — 実際にそうなっていた)。
export default function AssistantToggle() {
  const { mode, busy, choose } = useAnswerMode();
  if (mode === null) {
    return (
      <div className="set-row">
        <span className="muted">読み込み中…</span>
      </div>
    );
  }
  const current = ANSWER_MODE_UI.find((m) => m.key === mode) ?? ANSWER_MODE_UI[0];
  return (
    <div className="set-row">
      <div className="set-row-text">
        <div className="set-row-title">応答モード</div>
        <div className="set-row-desc">{current.desc}</div>
      </div>
      <ModeButtons mode={mode} busy={busy} choose={choose} />
    </div>
  );
}
