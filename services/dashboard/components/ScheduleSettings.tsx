"use client";

import { useEffect, useState } from "react";
import type { Schedule } from "@/lib/schedule";
import type { ModeInfo } from "@/lib/answerMode";
import Toggle from "./Toggle";
import WeekEditor from "./WeekEditor";
import { ANSWER_MODE_UI, modeInfoText } from "./AnswerModeControl";

// 応答モードの時間割 (2026-09-29)。
// 例: 平日 8:00〜17:00 はスタンバイ、それ以外は不在 (ノータイムで AI)。
// 手で切り替えたときは次の切り替わりまでそちらが勝ち、そこで時間割に戻る (hookd の schedule.py)。
export default function ScheduleSettings() {
  const [sched, setSched] = useState<Schedule | null>(null);
  const [info, setInfo] = useState<ModeInfo | null>(null);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [closedText, setClosedText] = useState("");

  useEffect(() => {
    fetch("/api/schedule", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("schedule"))))
      .then((d) => {
        setSched(d.schedule);
        setInfo(d.mode_info);
        setClosedText((d.schedule.closed ?? []).join("\n"));
      })
      .catch(() => setError("読み込めませんでした"));
  }, []);

  const change = (next: Schedule) => {
    setSched(next);
    setDirty(true);
  };

  const save = async (next: Schedule = sched!) => {
    setBusy(true);
    setError("");
    try {
      const closed = closedText
        .split(/[\s,、]+/)
        .map((s) => s.trim().replace(/\//g, "-"))
        .filter(Boolean);
      const res = await fetch("/api/schedule", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...next, closed }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(d.error ?? "保存できませんでした");
        return;
      }
      setSched(d.schedule);
      setInfo(d.mode_info);
      setClosedText(d.schedule.closed.join("\n"));
      setDirty(false);
    } finally {
      setBusy(false);
    }
  };

  if (!sched) {
    return (
      <div className="set-group">
        <div className="set-row">
          <span className="muted">{error || "読み込み中…"}</span>
        </div>
      </div>
    );
  }

  return (
    <div className="set-group">
      <div className="set-row inline">
        <div className="set-row-text">
          <div className="set-row-title">時間割で切り替える</div>
          <div className="set-row-desc">{info ? modeInfoText(info) : ""}</div>
        </div>
        {/* オン/オフはその場で保存する (編集中の時間帯もいっしょに) */}
        <Toggle
          on={sched.enabled}
          label="時間割で切り替える"
          disabled={busy}
          onChange={(on) => save({ ...sched, enabled: on })}
        />
      </div>
      {sched.enabled && (
        <>
          <div className="set-row">
            <WeekEditor
              days={sched.days}
              withMode
              onChange={(days) => change({ ...sched, days })}
            />
          </div>
          <div className="set-row inline">
            <div className="set-row-text">
              <div className="set-row-title">時間帯の外</div>
              <div className="set-row-desc">どの時間帯にも入らないとき (夜・休みの日)</div>
            </div>
            <select
              className="set-select"
              value={sched.default}
              onChange={(e) => change({ ...sched, default: e.target.value as Schedule["default"] })}
            >
              {ANSWER_MODE_UI.map((m) => (
                <option key={m.key} value={m.key}>
                  {m.label}
                </option>
              ))}
            </select>
          </div>
          <div className="set-row">
            <div className="set-row-title">休業日</div>
            <div className="set-row-desc">
              年末年始・お盆など。祝日と同じく「祝」の行が使われます。1 行に 1 日 (2026-12-29)
            </div>
            <textarea
              className="set-textarea"
              rows={3}
              value={closedText}
              onChange={(e) => {
                setClosedText(e.target.value);
                setDirty(true);
              }}
            />
          </div>
          <div className="set-row inline">
            <div className="set-row-desc" style={{ color: error ? "#f87171" : undefined }}>
              {error || (dirty ? "保存していない変更があります" : "祝日は自動で「祝」の行になります")}
            </div>
            <button className="btn-quiet" onClick={() => save()} disabled={busy || !dirty}>
              保存
            </button>
          </div>
        </>
      )}
    </div>
  );
}
