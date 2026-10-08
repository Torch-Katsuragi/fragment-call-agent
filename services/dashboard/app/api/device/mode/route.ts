import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { ANSWER_MODES, aiAnswers, isAnswerMode, setModeByHand } from "@/lib/answerMode";

export const dynamic = "force-dynamic";

// 端末から応答モードを変える (常駐通知の「スタンバイ / 解除」ボタン)。
//
// ⚠**スタンバイは本人の明示操作**。アプリの起動状態や画面の点灯から推測して
//   ここを叩いてはいけない (見ていないのに鳴り続ける端末になる)。
// ⚠スタンバイの切り忘れ対策 (8 時間で不在へ) と時間割への戻りはサーバー (hookd の schedule.py) が持つ
//   (2026-09-29 に端末のタイマーから移した)。
export async function POST(req: NextRequest) {
  let mode = "";
  try {
    mode = String((await req.json())?.mode ?? "");
  } catch {
    return NextResponse.json({ ok: false, error: "bad body" }, { status: 400 });
  }
  if (!isAnswerMode(mode)) {
    return NextResponse.json(
      { ok: false, error: `mode は ${ANSWER_MODES.join(" / ")} のいずれか` },
      { status: 400 },
    );
  }
  const info = await setModeByHand(mode);
  if (info) return NextResponse.json({ ok: true, answer_mode: mode, mode_info: info });
  // hookd が落ちているときだけ直に書く。古い2値も揃える (着信バナー等がまだ assistant_enabled を見ている)
  for (const [key, value] of [
    ["answer_mode", mode],
    ["assistant_enabled", String(aiAnswers(mode))],
  ]) {
    await pool.query(
      `INSERT INTO settings (key, value) VALUES ($1, $2)
       ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = now()`,
      [key, value],
    );
  }
  return NextResponse.json({ ok: true, answer_mode: mode });
}
