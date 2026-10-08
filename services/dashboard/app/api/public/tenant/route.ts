import { NextRequest, NextResponse } from "next/server";
import { publicBase } from "@/lib/publicBase";
import { DEFAULT_TENANT, TENANT_COOKIE, listTenants } from "@/lib/tenants";

export const dynamic = "force-dynamic";

// 管制室 (テナント) を切り替える (2026-10-04、同居構成)。?to=<id>&next=<戻り先>
// Cookie fr_tenant を付け替えるだけ。Caddy がこの Cookie で行き先の管制室を決める。
// ⚠ログイン無しで呼べてよい: Cookie は「どの管制室に行くか」を決めるだけで、入れるかは
//   行き先の管制室が自分のメンバー表で毎回判定する (lib/members.ts)
export async function GET(req: NextRequest) {
  const to = req.nextUrl.searchParams.get("to") ?? "";
  const nextRaw = req.nextUrl.searchParams.get("next") ?? "/";
  // 戻り先はこのサイトの中だけ (//evil.example のような外への飛び先を受けない)
  const next = /^\/(?![/\\])/.test(nextRaw) && !nextRaw.includes("\\") ? nextRaw : "/";
  // ⚠戻り先の URL は公開の URL で組む (Caddy 越しだと req の Host は localhost になる。lib/publicBase.ts)
  const base = publicBase(req.nextUrl.origin);
  const res = NextResponse.redirect(base + next);
  if (!listTenants().some((t) => t.id === to)) return res;
  if (to === DEFAULT_TENANT) {
    res.cookies.delete(TENANT_COOKIE);
  } else {
    res.cookies.set(TENANT_COOKIE, to, {
      path: "/",
      httpOnly: true,
      secure: base.startsWith("https:"),
      sameSite: "lax",
      maxAge: 60 * 60 * 24 * 365,
    });
  }
  return res;
}
