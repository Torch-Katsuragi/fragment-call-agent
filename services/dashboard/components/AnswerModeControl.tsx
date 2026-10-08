"use client";

import { useEffect, useState } from "react";
import type { ModeInfo } from "@/lib/answerMode";

// 応答モードの3択スイッチ。2026-08-01に留守電ON/OFFの2値から広げた。
//
// ⚠**トップと設定ページで同じものを使う**。以前はそれぞれが独立した真偽値トグルを持っていて、
//   3状態にした後もトップだけ2値のままだと、**スタンバイ中にトップを触った瞬間に
//   スタンバイが消える**（真偽値では標準/不在の区別が付かないため）。
// ⚠スタンバイは本人の明示操作。ブラウザやアプリを開いていることから推測しない。
// ⚠不在の「即座に応答」は消さないこと — 1コールで切って折り返させ通話料を相手に
//   押しつける手合いへの対策（2026-07-30）。猶予を入れるとこの対策が抜ける。
export const ANSWER_MODE_UI = [
  { key: "away", label: "不在", desc: "すぐにAIが出ます" },
  // ⚠秒数を文言に焼かない (2026-09-18)。鳴らす秒数はサーバーの STANDBY_RING_SEC で、
  //   この日 2→8 に変えた。文言側に数字があると設定と食い違ったまま出続ける
  { key: "standby", label: "スタンバイ", desc: "数秒鳴らし、出なければAIが出ます" },
  { key: "manual", label: "自分で出る", desc: "AIは出ません。出るまで鳴らします" },
] as const;

/** 時間割の状態を一行で (2026-09-29)。例「時間割 · 17:00 に不在へ」「手動 · 明日 8:00 に時間割へ戻る」 */
export function modeInfoText(info: ModeInfo): string {
  const label = (m: string | null) => ANSWER_MODE_UI.find((x) => x.key === m)?.label ?? "";
  const at = info.next_at ? whenText(info.next_at * 1000) : "";
  if (info.source === "manual") {
    return info.schedule_enabled
      ? `手動で切り替え中 · ${at} に時間割へ戻ります`
      : `${at} に${label(info.next_mode)}へ戻ります`;
  }
  if (info.source === "schedule") {
    return at ? `時間割どおり · ${at} に${label(info.next_mode)}へ` : "時間割どおり";
  }
  return "時間割は使っていません";
}

function whenText(ms: number): string {
  const d = new Date(ms);
  const now = new Date();
  const hm = `${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(d) - day(now)) / 86400000);
  if (diff === 0) return hm;
  if (diff === 1) return `明日 ${hm}`;
  return `${d.getMonth() + 1}/${d.getDate()}(${"日月火水木金土"[d.getDay()]}) ${hm}`;
}

export function useAnswerMode() {
  const [mode, setMode] = useState<string | null>(null);
  const [info, setInfo] = useState<ModeInfo | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    fetch("/api/settings", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("settings"))))
      .then((d) => {
        setMode(d.answer_mode ?? (d.assistant_enabled ? "away" : "manual"));
        setInfo(d.mode_info ?? null);
      })
      .catch(() => setMode(null));
  }, []);

  const choose = async (next: string) => {
    if (busy || next === mode) return;
    setBusy(true);
    const prev = mode;
    setMode(next); // 楽観更新 — 電話が鳴っている最中に切り替えることがあるので待たせない
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answer_mode: next }),
      });
      if (res.ok) {
        const d = await res.json();
        setMode(d.answer_mode);
        if (d.mode_info) setInfo(d.mode_info);
      } else setMode(prev);
    } catch {
      setMode(prev);
    } finally {
      setBusy(false);
    }
  };

  return { mode, info, busy, choose };
}

/**
 * ボタンだけの見た目部品。⚠状態は**必ず呼び出し元から渡す** —
 * ここで useAnswerMode() を呼ぶと、行の説明文を出す親と状態が二重になり、
 * 押しても説明文が古いままになる。
 */
export function ModeButtons({
  mode,
  busy,
  choose,
}: {
  mode: string;
  busy: boolean;
  choose: (m: string) => void;
}) {
  return (
    <div className="segmented" role="group" aria-label="応答モード">
      {ANSWER_MODE_UI.map((m) => (
        <button
          key={m.key}
          className={`segmented-btn ${m.key === mode ? "on" : ""}`}
          onClick={() => choose(m.key)}
          disabled={busy}
          aria-pressed={m.key === mode}
          title={m.desc}
        >
          {m.label}
        </button>
      ))}
    </div>
  );
}

/**
 * トップのヘッダー用。**表示だけ** (2026-09-19 ユーザー「設定にあるなら管制室で応答モードは
 * 設定しなくていい」)。切り替えは設定ページ (AssistantToggle) とアプリの常駐通知に集約した。
 * ここは「いまどのモードか」を見せて、押したら設定へ飛ぶ。
 */
export default function AnswerModeControl() {
  const { mode } = useAnswerMode();
  if (mode === null) return null;
  const ui = ANSWER_MODE_UI.find((m) => m.key === mode);
  return (
    <div className="mode-block">
      <a className="mode-status" href="/settings" title="応答モードは設定ページで切り替えます">
        <span className={`mode-dot ${mode}`} />
        {ui?.label ?? mode}
        <span className="muted"> ▸ 設定</span>
      </a>
      <div className="mode-desc">{ui?.desc}</div>
    </div>
  );
}

/** 旧: トップでも切り替えられる版。設定ページの AssistantToggle が同じ部品 (ModeButtons) を使う */
export function AnswerModeSwitcher() {
  const { mode, busy, choose } = useAnswerMode();
  if (mode === null) return null;
  const desc = ANSWER_MODE_UI.find((m) => m.key === mode)?.desc;
  return (
    // ⚠**`.toolbar` の直接の子であること**が前提 (2026-09-18)。スマホ用CSSは
    //   `order` で並べ替えるが、order はフレックスコンテナの**直接の子にしか効かない**。
    //   以前は page.tsx が操作類を無名の div で包んでいたため、mobile.css の
    //   `.toolbar .segmented { order: 3 }` が丸ごと無視され、375px幅で
    //   ボタンが縦書きに潰れてタイトルに重なっていた (実機幅で確認)。
    <div className="mode-block">
      <ModeButtons mode={mode} busy={busy} choose={choose} />
      {/* ⚠スマホには hover が無く、各ボタンの title 属性の説明が**読めない**。
          選択中のものだけ本文として出す (PCでは tooltip があるのでCSSで隠す) */}
      <div className="mode-desc">{desc}</div>
    </div>
  );
}
