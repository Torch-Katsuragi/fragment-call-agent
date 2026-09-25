import { NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { phonebookName } from "@/lib/phonebook";

export const dynamic = "force-dynamic";

const HOOKD = process.env.HOOKD_URL ?? "http://127.0.0.1:8790";

// 着信中の呼の状態 (hookdのin-memory状態をプロキシ) + 留守電トグル。
// 管制室の着信バナーが1.5秒おきにポーリングする
export async function GET() {
  let ringing = null;
  try {
    const res = await fetch(`${HOOKD}/ringing_state`, { cache: "no-store" });
    if (res.ok) ringing = (await res.json()).ringing;
  } catch {
    // hookd停止中はバナーなし (呼の処理には影響しない)
  }
  let assistantEnabled = true;
  try {
    const r = await pool.query(
      "SELECT value FROM settings WHERE key = 'assistant_enabled'"
    );
    assistantEnabled = r.rows.length === 0 || r.rows[0].value !== "false";
  } catch {
    // DB不調時はON扱い
  }
  // 番号のweb検索結果 (workerが number_lookup ジョブで調べたもの) をバナーに添える
  let lookup: { verdict: string; summary: string } | null = null;
  let callerName: string | null = null;
  if (ringing?.number) {
    callerName = phonebookName(ringing.number);
    try {
      const r = await pool.query(
        `SELECT result FROM agent_jobs
         WHERE kind = 'number_lookup' AND query = $1 AND status = 'done'
         ORDER BY id DESC LIMIT 1`,
        [ringing.number],
      );
      if (r.rows.length > 0 && r.rows[0].result) lookup = JSON.parse(r.rows[0].result);
    } catch {
      /* 検索未完了 */
    }
  }
  return NextResponse.json({
    ringing,
    assistant_enabled: assistantEnabled,
    caller_name: callerName,
    lookup,
  });
}
