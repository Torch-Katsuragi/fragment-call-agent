import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { forget, memberFromHeaders } from "@/lib/members";
import { atLeast } from "@/lib/roles";
import { parseHours } from "@/lib/schedule";

export const dynamic = "force-dynamic";

// 人ごとの受付時間 (2026-09-29)。その人の端末を鳴らしてよい時間。null = いつでも。
// ⚠変えられるのは本人と管理以上。id に "me" を渡すと自分 (アプリはメンバー番号を知らない)
async function target(req: NextRequest, raw: string) {
  const me = memberFromHeaders(req.headers);
  if (!me) return { error: NextResponse.json({ error: "ログインが必要です" }, { status: 401 }) };
  const id = raw === "me" ? me.id : Number(raw);
  if (!id) return { error: NextResponse.json({ error: "いません" }, { status: 404 }) };
  return { me, id };
}

export async function GET(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const t = await target(req, (await ctx.params).id);
  if ("error" in t) return t.error;
  const r = await pool.query("SELECT hours FROM members WHERE id = $1", [t.id]);
  if (!r.rows[0]) return NextResponse.json({ error: "いません" }, { status: 404 });
  return NextResponse.json({ hours: r.rows[0].hours ?? null });
}

export async function PUT(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const t = await target(req, (await ctx.params).id);
  if ("error" in t) return t.error;
  if (t.id !== t.me.id && !atLeast(t.me.role, "admin")) {
    return NextResponse.json({ error: "ほかの人の受付時間は管理以上が変えられます" }, { status: 403 });
  }
  const body = await req.json().catch(() => undefined);
  const h = parseHours(body?.hours);
  if (typeof h === "string") return NextResponse.json({ error: h }, { status: 400 });
  const r = await pool.query("UPDATE members SET hours = $2 WHERE id = $1 RETURNING id", [
    t.id,
    h === null ? null : JSON.stringify(h),
  ]);
  if (!r.rows[0]) return NextResponse.json({ error: "いません" }, { status: 404 });
  forget();
  return NextResponse.json({ hours: h });
}
