"use client";

import { DAY_KEYS, DAY_LABEL, type Block, type DayKey } from "@/lib/schedule";
import { ANSWER_MODE_UI } from "./AnswerModeControl";

// 曜日ごとの時間帯の編集 (2026-09-29)。回線の時間割 (withMode) と人ごとの受付時間で共用。
// 1 行 = 1 曜日。「祝」は祝日と休業日。終わりが始まり以前なら日をまたぐ (22:00〜06:00)。
export type Days = Partial<Record<DayKey, Block[]>>;

const WEEKDAYS: DayKey[] = ["mon", "tue", "wed", "thu", "fri"];

export default function WeekEditor({
  days,
  onChange,
  withMode,
  defaultMode = "standby",
}: {
  days: Days;
  onChange: (next: Days) => void;
  withMode: boolean;
  defaultMode?: string;
}) {
  const set = (k: DayKey, list: Block[]) => {
    const next = { ...days };
    if (list.length) next[k] = list;
    else delete next[k];
    onChange(next);
  };
  const edit = (k: DayKey, i: number, patch: Partial<Block>) =>
    set(k, (days[k] ?? []).map((b, j) => (j === i ? { ...b, ...patch } : b)));
  const add = (k: DayKey) => {
    const last = (days[k] ?? []).at(-1);
    const b: Block = last ? { ...last } : { start: "08:00", end: "17:00" };
    if (withMode && !b.mode) b.mode = defaultMode as Block["mode"];
    set(k, [...(days[k] ?? []), b]);
  };
  // 月曜の時間帯を火〜金へ写す (平日が同じなのが普通なので)
  const copyWeekdays = () => {
    const src = days.mon ?? [];
    const next = { ...days };
    for (const k of WEEKDAYS.slice(1)) {
      if (src.length) next[k] = src.map((b) => ({ ...b }));
      else delete next[k];
    }
    onChange(next);
  };

  return (
    <div className="week">
      {DAY_KEYS.map((k) => (
        <div key={k} className={`week-row ${k === "sat" || k === "sun" || k === "hol" ? "off" : ""}`}>
          <span className="week-day">{DAY_LABEL[k]}</span>
          <div className="week-blocks">
            {(days[k] ?? []).map((b, i) => (
              <div key={i} className="week-block">
                <input type="time" value={b.start} onChange={(e) => edit(k, i, { start: e.target.value })} />
                <span className="muted">〜</span>
                <input type="time" value={b.end} onChange={(e) => edit(k, i, { end: e.target.value })} />
                {withMode && (
                  <select
                    value={b.mode}
                    onChange={(e) => edit(k, i, { mode: e.target.value as Block["mode"] })}
                  >
                    {ANSWER_MODE_UI.map((m) => (
                      <option key={m.key} value={m.key}>
                        {m.label}
                      </option>
                    ))}
                  </select>
                )}
                <button
                  className="btn-quiet"
                  aria-label="この時間帯を消す"
                  onClick={() => set(k, (days[k] ?? []).filter((_, j) => j !== i))}
                >
                  ×
                </button>
              </div>
            ))}
            <div className="week-actions">
              <button className="btn-quiet" onClick={() => add(k)}>
                ＋ 時間帯
              </button>
              {k === "mon" && (
                <button className="btn-quiet" onClick={copyWeekdays}>
                  火〜金にも同じ
                </button>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
