import { NextResponse } from "next/server";
import { auth } from "@/auth";
import { extractDeviceToken } from "@/lib/deviceToken";
import { identifyDevice, identifyEmail, type Who } from "@/lib/members";
import { atLeast, requiredRole } from "@/lib/roles";
import { TENANT_ID, tenantsOfEmail } from "@/lib/tenants";

// 管制室の入口 (2026-09-26 にメンバーと権限の判定へ)。
// ⚠Node で動かす (runtime: nodejs)。以前は Edge で DB が引けず、端末トークンは署名だけで判定していた =
//   1 台だけ締め出すことができなかった。今は毎回メンバー表を見る (lib/members.ts、5 秒だけ覚える)
// ⚠判定した結果を x-member-* ヘッダでルートに渡す。外から同じ名前のヘッダを付けられても必ず上書きする
export default auth(async (req) => {
  const { pathname } = req.nextUrl;
  const need = requiredRole(req.method, pathname);
  const headers = new Headers(req.headers);
  for (const h of ["x-member-id", "x-member-role", "x-member-name", "x-member-email", "x-member-device"]) {
    headers.delete(h);
  }
  if (need === "public") return NextResponse.next({ request: { headers } });

  let who: Who | null = null;
  if (process.env.NODE_ENV === "development") {
    // ローカル開発 (next dev) だけ認証を省く。⚠本番ビルドでは必ず判定する (ホスト名では判定しない。
    //   Caddy が Host: localhost:3000 で転送するので、公開 URL でも素通りになった — 2026-07-26 の実測)
    who = { member: { id: 0, email: "dev@localhost", name: "開発", role: "owner", status: "active" }, deviceId: null, via: "dev" };
  } else if (req.auth?.user?.email) {
    who = await identifyEmail(req.auth.user.email);
  } else {
    const token = extractDeviceToken(req);
    if (token) who = await identifyDevice(token);
  }

  const api = pathname.startsWith("/api/");
  // 同居構成 (2026-10-04): ここのメンバーではないが他の管制室のメンバーなら、そちらへ回す。
  //   入口は fragment.example.com 1 つで、どの管制室に行くかは Cookie fr_tenant (Caddy が見る)
  if (!who && !api && req.auth?.user?.email) {
    const other = (await tenantsOfEmail(req.auth.user.email)).find((t) => t.id !== TENANT_ID);
    if (other) {
      const url = new URL("/api/public/tenant", req.nextUrl);
      url.searchParams.set("to", other.id);
      url.searchParams.set("next", req.nextUrl.pathname + req.nextUrl.search);
      return NextResponse.redirect(url);
    }
  }
  if (!who || who.member.status !== "active") {
    if (api) return NextResponse.json({ error: who ? "利用を止められています" : "ログインが必要です" }, { status: who ? 403 : 401 });
    const url = new URL("/login", req.nextUrl);
    if (who) url.searchParams.set("error", "banned");
    else url.searchParams.set("callbackUrl", req.nextUrl.pathname + req.nextUrl.search);
    return NextResponse.redirect(url);
  }
  if (!atLeast(who.member.role, need)) {
    return api
      ? NextResponse.json({ error: "この操作の権限がありません" }, { status: 403 })
      : NextResponse.redirect(new URL("/", req.nextUrl));
  }
  headers.set("x-member-id", String(who.member.id));
  headers.set("x-member-role", who.member.role);
  headers.set("x-member-name", encodeURIComponent(who.member.name));
  headers.set("x-member-email", who.member.email);
  if (who.deviceId) headers.set("x-member-device", who.deviceId);
  return NextResponse.next({ request: { headers } });
});

export const config = {
  runtime: "nodejs",
  // 静的アセットは除外。ログインの流れ (/api/auth) や招待は requiredRole が "public" にする
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"],
};
