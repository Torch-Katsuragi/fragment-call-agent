import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { EMPTY_SCHEDULE, parseSchedule } from "@/lib/schedule";
import { modeInfo } from "@/lib/answerMode";

export const dynamic = "force-dynamic";

// 応答モードの時間割 (2026-09-29)。settings.schedule に JSON で持つ。書くのは管理以上 (roles.ts)。
// ⚠判定は hookd (services/agent/schedule.py)。保存したら次の着信から効く
export async function GET() {
  const r = await pool.query("SELECT value FROM settings WHERE key = 'schedule'");
  let sched = EMPTY_SCHEDULE;
  try {
    const p = parseSchedule(JSON.parse(r.rows[0]?.value ?? "null"));
    if (typeof p !== "string") sched = p;
  } catch {
    // 空のまま
  }
  return NextResponse.json({ schedule: sched, mode_info: await modeInfo(async () => new Map()) });
}

export async function PUT(req: NextRequest) {
  const p = parseSchedule(await req.json().catch(() => null));
  if (typeof p === "string") return NextResponse.json({ error: p }, { status: 400 });
  await pool.query(
    `INSERT INTO settings (key, value) VALUES ('schedule', $1)
     ON CONFLICT (key) DO UPDATE SET value = $1, updated_at = now()`,
    [JSON.stringify(p)],
  );
  // ⚠時間割を入れ直したら手動の上書きは捨てる — 「保存したのに反映されない」を避ける
  await pool.query("UPDATE settings SET value = '', updated_at = now() WHERE key = 'answer_mode_until'");
  return NextResponse.json({ schedule: p, mode_info: await modeInfo(async () => new Map()) });
}
