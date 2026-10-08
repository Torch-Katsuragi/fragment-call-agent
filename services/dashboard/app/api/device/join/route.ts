import { NextRequest, NextResponse } from "next/server";
import { acceptInvite, createDeviceSession, hashToken, verifyGoogleIdToken } from "@/lib/members";
import { publicBase } from "@/lib/publicBase";
import { TENANT_ID, forwardTo, tenantOfInvite } from "@/lib/tenants";

export const dynamic = "force-dynamic";

// アプリが招待リンクから参加する (2026-09-26)。ログイン無しで呼べる (middleware の public)。
// {invite, id_token, device_id} → Google の身元証明を確かめ、招待したアドレスと同じなら
// メンバーにして、この端末のログイン (token) を返す。⚠違うアカウントでは受けられない
export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  const invite = typeof body?.invite === "string" ? body.invite : "";
  const idToken = typeof body?.id_token === "string" ? body.id_token : "";
  const deviceId = typeof body?.device_id === "string" ? body.device_id : "";
  if (!invite || !idToken || !/^[A-Za-z0-9-]{8,64}$/.test(deviceId)) {
    return NextResponse.json({ error: "入力が足りません" }, { status: 400 });
  }
  // 同居構成 (2026-10-04): 招待が他のテナントのものなら、そのテナントの管制室に渡す
  if (!req.headers.get("x-forwarded-tenant-hop")) {
    const owner = await tenantOfInvite(hashToken(invite));
    if (owner && owner.id !== TENANT_ID) return forwardTo(owner, "/api/device/join", body);
  }
  const g = await verifyGoogleIdToken(idToken);
  if (!g) return NextResponse.json({ error: "Google アカウントを確かめられませんでした" }, { status: 401 });
  const r = await acceptInvite(invite, g.email, g.name);
  if (!r.ok) return NextResponse.json({ error: r.error }, { status: 403 });
  const token = await createDeviceSession(r.member.id, deviceId);
  return NextResponse.json({
    token,
    base: publicBase(req.nextUrl.origin),
    member: { name: r.member.name, email: r.member.email, role: r.member.role },
  });
}
