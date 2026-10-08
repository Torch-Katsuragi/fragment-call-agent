// 応答モードの時間割と、人ごとの受付時間の形 (2026-09-29)。
// ⚠判定 (いまどのモードか・祝日か) は hookd の services/agent/schedule.py だけが行う。
//   ここは保存する前の形の検査だけ。祝日の表をこちらに持たないこと (二重になって食い違う)。
import { isAnswerMode, type AnswerMode } from "./answerMode";

export const DAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun", "hol"] as const;
export type DayKey = (typeof DAY_KEYS)[number];
export const DAY_LABEL: Record<DayKey, string> = {
  mon: "月", tue: "火", wed: "水", thu: "木", fri: "金", sat: "土", sun: "日", hol: "祝",
};

export type Block = { start: string; end: string; mode?: AnswerMode };
export type Schedule = {
  enabled: boolean;
  default: AnswerMode;
  days: Partial<Record<DayKey, Block[]>>;
  /** 休業日 YYYY-MM-DD。祝日と同じ扱い (hol の欄) */
  closed: string[];
};
export type Hours = { days: Partial<Record<DayKey, Block[]>> } | null;

const HHMM = /^([01]\d|2[0-3]):[0-5]\d$|^24:00$/;

function blocks(v: unknown, withMode: boolean): Block[] | string {
  if (!Array.isArray(v)) return "時間帯の形が不正です";
  const out: Block[] = [];
  for (const b of v.slice(0, 8)) {
    const start = String(b?.start ?? "");
    const end = String(b?.end ?? "");
    if (!HHMM.test(start) || !HHMM.test(end)) return `時刻が不正です (${start}〜${end})`;
    if (start === end) return `始まりと終わりが同じです (${start})`;
    if (withMode) {
      const mode = String(b?.mode ?? "");
      if (!isAnswerMode(mode)) return "時間帯のモードが不正です";
      out.push({ start, end, mode });
    } else out.push({ start, end });
  }
  return out;
}

function days(v: unknown, withMode: boolean): Schedule["days"] | string {
  const out: Schedule["days"] = {};
  if (!v || typeof v !== "object") return out;
  for (const k of DAY_KEYS) {
    const raw = (v as Record<string, unknown>)[k];
    if (raw === undefined) continue;
    const b = blocks(raw, withMode);
    if (typeof b === "string") return b;
    if (b.length) out[k] = b;
  }
  return out;
}

export function parseSchedule(v: unknown): Schedule | string {
  if (!v || typeof v !== "object") return "JSONが必要です";
  const o = v as Record<string, unknown>;
  const d = days(o.days, true);
  if (typeof d === "string") return d;
  const def = String(o.default ?? "away");
  if (!isAnswerMode(def)) return "時間帯の外のモードが不正です";
  const closed = Array.isArray(o.closed)
    ? o.closed.map(String).filter((x) => /^\d{4}-\d{2}-\d{2}$/.test(x)).slice(0, 100)
    : [];
  return { enabled: o.enabled === true, default: def, days: d, closed: [...new Set(closed)].sort() };
}

/** null = いつでも (受付時間を決めていない) */
export function parseHours(v: unknown): Hours | string {
  if (v === null) return null;
  if (!v || typeof v !== "object") return "JSONが必要です";
  const d = days((v as Record<string, unknown>).days, false);
  if (typeof d === "string") return d;
  return Object.keys(d).length ? { days: d } : null;
}

export const EMPTY_SCHEDULE: Schedule = {
  enabled: false,
  default: "away",
  days: {},
  closed: [],
};
