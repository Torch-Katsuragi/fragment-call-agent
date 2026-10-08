// 応答モード。2026-08-01に留守電ON/OFFの2値から3状態に変更した。
//
//   away    不在      … ノータイムでAIが出る
//   standby スタンバイ … 端末を2秒だけ鳴らし、出なければAIに渡す
//   manual  自分で出る … AIは出ない。受話を待って鳴らし続ける (書記 = 文字起こし+吹き出しは動く)
//   ⚠「録音のみ」(record、AI を同席させない) は 2026-09-19 に足して 2026-09-24 にユーザーの判断でボツ
//
// ⚠**スタンバイは本人の明示操作**であって、ブラウザやアプリの起動状態から推測しない。
// ⚠ここを route.ts に置くと Next.js のビルドが落ちる (route は決められた名前しか
//   export できない)。共有する定数・関数は lib に置くこと。
// ⚠hookd._answer_mode と同じ規則にすること。ズレるとダイヤルプランと画面で食い違う。
export const ANSWER_MODES = ["away", "standby", "manual"] as const;
export type AnswerMode = (typeof ANSWER_MODES)[number];

export function isAnswerMode(v: string): v is AnswerMode {
  return (ANSWER_MODES as readonly string[]).includes(v);
}

/** そのモードで AI が電話に出るか (旧 assistant_enabled の値)。manual だけ出ない */
export function aiAnswers(mode: string): boolean {
  return mode !== "manual";
}

/** answer_mode を正として読む。⚠未設定のときだけ旧 assistant_enabled から導出 (移行期の後方互換) */
export function deriveAnswerMode(got: Map<string, string>): AnswerMode {
  const m = got.get("answer_mode");
  if (m && isAnswerMode(m)) return m;
  return got.get("assistant_enabled") === "false" ? "manual" : "away";
}

// --- 時間割 (2026-09-29) ---
// ⚠いまのモードは hookd が決める (時間割・手動の上書き・祝日。services/agent/schedule.py)。
//   settings.answer_mode は「手で選んだ値」で、時間割が効いているいまのモードとは限らない。
//   画面は必ず modeInfo() を見ること。hookd が落ちているときだけ settings から読む
const HOOKD = process.env.HOOKD_URL ?? "http://127.0.0.1:8790";

export type ModeInfo = {
  mode: AnswerMode;
  /** schedule = 時間割どおり / manual = 手で切り替えた (next_at まで) / fixed = 時間割なし */
  source: "schedule" | "manual" | "fixed";
  schedule_enabled: boolean;
  /** 次に変わる時刻 (epoch 秒) とそのモード。無ければ null */
  next_at: number | null;
  next_mode: AnswerMode | null;
};

export async function modeInfo(
  fallback: () => Promise<Map<string, string>>,
): Promise<ModeInfo> {
  try {
    const res = await fetch(`${HOOKD}/answer_mode_info`, { cache: "no-store" });
    if (res.ok) return (await res.json()) as ModeInfo;
  } catch {
    // 下へ
  }
  return {
    mode: deriveAnswerMode(await fallback()),
    source: "fixed",
    schedule_enabled: false,
    next_at: null,
    next_mode: null,
  };
}

/** 手で切り替える (時間割が有効なら次の切り替わりまで)。hookd が落ちていれば null */
export async function setModeByHand(mode: AnswerMode): Promise<ModeInfo | null> {
  try {
    const res = await fetch(`${HOOKD}/answer_mode_set?mode=${mode}`, { cache: "no-store" });
    if (res.ok) return (await res.json()) as ModeInfo;
  } catch {
    // 呼び出し元が settings に直に書く
  }
  return null;
}
