import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/auth";
import { signDeviceToken } from "@/lib/deviceToken";
import { fcmClientConfig } from "@/lib/env";

export const dynamic = "force-dynamic";

// 端末のペアリング。**ブラウザでGoogleログイン済みのときだけ**新しい端末トークンを発行する。
//
// ⚠端末トークンでここを叩いても発行できない。`auth()` はセッション (Cookie) を見るので、
//   端末トークンで入ってきたリクエストには user が無い。
//   これが無いと、漏れた1本のトークンから無限に新しいトークンを作れてしまう。
//
// 返り値はそのままQRにできる形。⚠**接続先URLを一緒に返すのが要点** —
// 顧客ごとにVMが違う (シングルテナント複製) ので、中央の振り分けを作らない代わりに
// URLを端末へ配る必要がある (設計md「どのサーバーに繋ぐか」)。
export async function GET(req: NextRequest) {
  // ローカル開発 (next dev) は認証ごとバイパスされる (auth.ts の authorized と同じ流儀)。
  // 本番ビルドでは必ずログイン済みセッションを要求する
  if (process.env.NODE_ENV !== "development") {
    const session = await auth();
    if (!session?.user) {
      return NextResponse.json({ error: "sign in required" }, { status: 401 });
    }
  }

  const name = req.nextUrl.searchParams.get("name")?.slice(0, 40) || "スマホ";
  let token: string;
  try {
    token = await signDeviceToken(name);
  } catch {
    // AUTH_SECRET 未設定。⚠本番では next-auth 自体が要求するので、通常ここには来ない
    return NextResponse.json({ error: "AUTH_SECRET is not configured" }, { status: 500 });
  }

  // アプリが繋ぐ先。X-Forwarded-* はCaddyが付ける (trustHost と同じ前提)
  const proto = req.headers.get("x-forwarded-proto") ?? "https";
  const host = req.headers.get("x-forwarded-host") ?? req.headers.get("host") ?? "";
  const url = `${proto}://${host}`;

  // firebase: プッシュ (FCM) の接続情報。無ければ null (アプリはロングポーリングのみ)。
  // ⚠QR にはこれを載せない (長くなる)。アプリは保存後に /api/device/config で取り直す
  return NextResponse.json({ url, token, name, firebase: fcmClientConfig() });
}
