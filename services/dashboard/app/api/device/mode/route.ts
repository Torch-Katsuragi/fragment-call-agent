import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { ANSWER_MODES, aiAnswers, isAnswerMode } from "@/lib/answerMode";

export const dynamic = "force-dynamic";

// 端末から応答モードを変える (常駐通知の「スタンバイ / 解除」ボタン)。
//
// ⚠**スタンバイは本人の明示操作**。アプリの起動状態や画面の点灯から推測して
//   ここを叩いてはいけない (見ていないのに鳴り続ける端末になる)。
//   端末側の時限つき解除は Prefs.standbyUntil が持っていて、切れたら away を送ってくる。
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
  // 古い2値も揃える (着信バナー等がまだ assistant_enabled を見ている)
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
