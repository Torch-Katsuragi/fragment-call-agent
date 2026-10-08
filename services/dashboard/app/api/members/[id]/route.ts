import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { forget, memberFromHeaders } from "@/lib/members";
import { isRole } from "@/lib/roles";

export const dynamic = "force-dynamic";

// メンバーの権限を変える・締め出す・戻す (2026-09-26)。⚠オーナーだけ。
// {role} / {status: "banned" | "active"} / {duty}。締め出すとその人の端末のログインも全部切る
// duty = 担当 (2026-10-04)。保留中に呼ぶ相手を AI が並べ替える材料。以前は端末ごとに持っていた
// ⚠最後のオーナーは下げられない・締め出せない (誰も管理できなくなる)
export async function PATCH(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const me = memberFromHeaders(req.headers);
  if (!me || (me.role !== "owner" && me.id !== 0)) {
    return NextResponse.json({ error: "メンバーの変更はオーナーだけができます" }, { status: 403 });
  }
  const id = Number((await ctx.params).id);
  const body = await req.json().catch(() => null);
  const role = body?.role;
  const status = body?.status;
  const duty = body?.duty;
  if (duty !== undefined && typeof duty !== "string") {
    return NextResponse.json({ error: "担当が不正です" }, { status: 400 });
  }
  if (role !== undefined && !isRole(role)) return NextResponse.json({ error: "権限が不正です" }, { status: 400 });
  if (status !== undefined && status !== "banned" && status !== "active") {
    return NextResponse.json({ error: "状態が不正です" }, { status: 400 });
  }
  const target = await pool.query("SELECT role, status FROM members WHERE id = $1", [id]);
  if (!target.rows[0]) return NextResponse.json({ error: "いません" }, { status: 404 });
  const demotesOwner =
    target.rows[0].role === "owner" && ((role && role !== "owner") || status === "banned");
  if (demotesOwner) {
    const owners = await pool.query("SELECT count(*) FROM members WHERE role = 'owner' AND status = 'active'");
    if (Number(owners.rows[0].count) <= 1) {
      return NextResponse.json({ error: "最後のオーナーは下げられません" }, { status: 409 });
    }
  }
  if (role) await pool.query("UPDATE members SET role = $2 WHERE id = $1", [id, role]);
  if (typeof duty === "string") {
    await pool.query("UPDATE members SET duty = $2 WHERE id = $1", [id, duty.trim().slice(0, 200)]);
  }
  if (status) {
    await pool.query("UPDATE members SET status = $2 WHERE id = $1", [id, status]);
    if (status === "banned") {
      await pool.query(
        "UPDATE device_sessions SET revoked_at = now() WHERE member_id = $1 AND revoked_at IS NULL",
        [id],
      );
    }
  }
  forget();
  return NextResponse.json({ ok: true });
}
