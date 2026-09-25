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
