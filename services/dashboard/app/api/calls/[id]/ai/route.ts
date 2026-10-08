import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";

export const dynamic = "force-dynamic";

// AI 応答のオン・オフ (2026-09-26)。「AIに任せる」= オンにしてから会話を抜ける。
// agent は agent_push で起きて読み直し、会話中の人がいなければ AI が応対に戻る
// (services/agent/presence.py)。⚠会話中の人がいる間はオンにしても AI は喋らない
export async function POST(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const body = await req.json().catch(() => null);
  if (typeof body?.on !== "boolean") {
    return NextResponse.json({ error: "on (true/false) が必要です" }, { status: 400 });
  }
  try {
    const r = await pool.query(
      "UPDATE calls SET ai_on = $2 WHERE id = $1 AND ended_at IS NULL",
      [id, body.on],
    );
    if (r.rowCount === 0) {
      return NextResponse.json({ error: "通話中ではありません" }, { status: 409 });
    }
    await pool.query("SELECT pg_notify('agent_push', $1)", [id]);
    return NextResponse.json({ ok: true, ai_on: body.on });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 502 });
  }
}
