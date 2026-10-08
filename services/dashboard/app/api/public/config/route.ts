import { NextRequest, NextResponse } from "next/server";
import { publicBase } from "@/lib/publicBase";

export const dynamic = "force-dynamic";

// ログイン前のアプリが知る必要のあること (2026-09-26)。⚠秘密は入れない (ログイン無しで読める)。
// google_client_id = 管制室の Web クライアント。アプリはこれを serverClientId にして Google ログインし、
// 受け取った ID トークンを /api/device/join か /api/device/login に渡す
export async function GET(req: NextRequest) {
  return NextResponse.json({
    name: "フラグメント",
    base: publicBase(req.nextUrl.origin),
    google_client_id: process.env.AUTH_GOOGLE_ID ?? "",
  });
}
