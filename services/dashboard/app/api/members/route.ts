import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { createInvite, hashToken, memberFromHeaders } from "@/lib/members";
import { currentTenant } from "@/lib/tenants";
import { mailEnabled, sendMail } from "@/lib/mail";
import { atLeast, isRole, ROLE_LABEL } from "@/lib/roles";
import { publicBase } from "@/lib/publicBase";

export const dynamic = "force-dynamic";

// メンバーと招待 (2026-09-26)。
// GET  … メンバー (端末の数つき) と、まだ受けられていない招待
// POST … 招待する {email, role}。招待リンクを作り、送信の設定があればメールで送る (返信は受けない)。
//        ⚠admin が招待できるのは応対・閲覧まで。owner と admin を増やせるのは owner だけ
export async function GET(req: NextRequest) {
  const me = memberFromHeaders(req.headers);
  const members = await pool.query(
    `SELECT m.id, m.email, m.name, m.role, m.status, m.created_at, m.hours, m.duty,
            count(s.*) FILTER (WHERE s.revoked_at IS NULL) AS devices
     FROM members m LEFT JOIN device_sessions s ON s.member_id = m.id
     GROUP BY m.id ORDER BY m.status, m.id`,
  );
  const invites = await pool.query(
    `SELECT token_hash, email, role, created_at, expires_at, mailed_at FROM invites
     WHERE accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now() ORDER BY created_at DESC`,
  );
  return NextResponse.json({
    members: members.rows.map((r) => ({ ...r, devices: Number(r.devices) })),
    invites: invites.rows,
    mail: mailEnabled(),
    me: me ? { id: me.id, role: me.role } : null,
  });
}

export async function POST(req: NextRequest) {
  const me = memberFromHeaders(req.headers);
  const body = await req.json().catch(() => null);
  const email = typeof body?.email === "string" ? body.email.trim().toLowerCase() : "";
  const role = body?.role;
  if (!me) return NextResponse.json({ error: "ログインが必要です" }, { status: 401 });
  if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
    return NextResponse.json({ error: "メールアドレスの形が正しくありません" }, { status: 400 });
  }
  if (!isRole(role)) return NextResponse.json({ error: "権限を選んでください" }, { status: 400 });
  if (atLeast(role, "admin") && me.role !== "owner" && me.id !== 0) {
    return NextResponse.json({ error: "オーナーと管理の招待はオーナーだけができます" }, { status: 403 });
  }
  const token = await createInvite(email, role, me.id);
  const link = `${publicBase(req.nextUrl.origin)}/invite/${token}`;
  // 管制室の名前を入れる (2026-10-04、同居構成)。どこからの招待か分からないと、受けた人が迷う
  const place = currentTenant()?.label;
  const mailed = await sendMail(
    email,
    place ? `フラグメント (${place}) への招待` : "フラグメントへの招待",
    `${me.name || me.email || "フラグメントの管理者"} さんから、電話エージェント「フラグメント」` +
      `${place ? `の「${place}」の管制室` : ""}に「${ROLE_LABEL[role]}」の権限で招待されました。\n\n` +
      `次のリンクを開き、このメールアドレス (${email}) の Google アカウントでログインしてください。` +
      `Android ではアプリが開きます。\n\n${link}\n\n` +
      `リンクは 7 日間有効です。心当たりがなければ、このメールは破棄してください。\n` +
      `このアドレスは送信専用です。返信はできません。`,
  );
  if (mailed) {
    // ⚠送った印はこの招待にだけ付ける (同じアドレスの別の招待まで「送った」にしない)
    await pool.query("UPDATE invites SET mailed_at = now() WHERE token_hash = $1", [hashToken(token)]);
  }
  return NextResponse.json({ ok: true, link, mailed });
}
