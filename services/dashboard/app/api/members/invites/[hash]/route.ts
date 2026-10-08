import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";

export const dynamic = "force-dynamic";

// 招待を取り消す (2026-09-26)。リンクはその時点で使えなくなる。権限は middleware (admin 以上)
export async function DELETE(_req: NextRequest, ctx: { params: Promise<{ hash: string }> }) {
  const { hash } = await ctx.params;
  await pool.query("UPDATE invites SET revoked_at = now() WHERE token_hash = $1 AND accepted_at IS NULL", [hash]);
  return NextResponse.json({ ok: true });
}
