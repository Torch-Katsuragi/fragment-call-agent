import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { createDeviceSession, memberByEmail, verifyGoogleIdToken } from "@/lib/members";
import { publicBase } from "@/lib/publicBase";
import { TENANT_ID, forwardTo, tenantsOfEmail } from "@/lib/tenants";

export const dynamic = "force-dynamic";

// すでにメンバーの人が、アプリで Google ログインする (2026-09-26。機種変更・入れ直し・旧式からの移行)。
// ログイン無しで呼べる (middleware の public)。{id_token, device_id} → この端末のログイン (token)
export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  const idToken = typeof body?.id_token === "string" ? body.id_token : "";
  const deviceId = typeof body?.device_id === "string" ? body.device_id : "";
  if (!idToken || !/^[A-Za-z0-9-]{8,64}$/.test(deviceId)) {
    return NextResponse.json({ error: "入力が足りません" }, { status: 400 });
  }
  const g = await verifyGoogleIdToken(idToken);
  if (!g) return NextResponse.json({ error: "Google アカウントを確かめられませんでした" }, { status: 401 });
  const m = await memberByEmail(g.email);
  if (!m) {
    // 同居構成 (2026-10-04): アプリは最初どのテナントか知らずにここへ来る。
    // 他のテナントのメンバーなら、そのテナントの管制室に渡す (トークンの頭にテナントが入る)
    if (!req.headers.get("x-forwarded-tenant-hop")) {
      const other = (await tenantsOfEmail(g.email)).find((t) => t.id !== TENANT_ID);
      if (other) return forwardTo(other, "/api/device/login", body);
    }
    return NextResponse.json({ error: `${g.email} はメンバーではありません。招待を受けてください` }, { status: 403 });
  }
  if (m.status !== "active") return NextResponse.json({ error: "このアカウントは利用を止められています" }, { status: 403 });
  // 名前は Google アカウントのものに揃える (管制室から入ったときと同じ。auth.ts の signIn)
  if (g.name && g.name !== m.name) {
    await pool.query("UPDATE members SET name = $2 WHERE id = $1", [m.id, g.name]).catch(() => {});
  }
  const token = await createDeviceSession(m.id, deviceId);
  return NextResponse.json({
    token,
    base: publicBase(req.nextUrl.origin),
    member: { name: m.name || g.name, email: m.email, role: m.role },
  });
}
