import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { deviceFrom } from "@/lib/device";

export const dynamic = "force-dynamic";

const HOOKD = process.env.HOOKD_URL ?? "http://127.0.0.1:8790";

// 保留中に呼ぶ相手の候補 (2026-09-26)。端末の名前と役割を、AI が会話の流れから並べ替えて返す
// (hookd の /transfer_candidates)。自分の端末は出さない
export async function GET(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const r = await pool.query("SELECT room_name FROM calls WHERE id = $1", [id]);
  if (r.rows.length === 0) {
    return NextResponse.json({ error: "not found" }, { status: 404 });
  }
  const q = new URLSearchParams({
    room: r.rows[0].room_name,
    exclude: deviceFrom(req)?.id ?? "",
  });
  try {
    const res = await fetch(`${HOOKD}/transfer_candidates?${q}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`hookd ${res.status}`);
    return NextResponse.json(await res.json());
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 502 });
  }
}
