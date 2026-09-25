import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";

export const dynamic = "force-dynamic";

// 端末の一時停止 (2026-09-24)。アプリの常駐通知・設定ページから呼ばれる。
//
// ⚠端末の識別には FCM トークン (device_push_tokens の主キー) を使う。端末トークン (認証) は
//   同じ名前で複数発行できるので端末を見分けられない。
// ⚠一時停止中の端末は hookd が「起きろ」を送らず、全端末が一時停止ならスタンバイの待ちを飛ばす
//   (hookd.pickup_wait)。端末側も自分で鳴らさない (二重の守り)
export async function POST(req: NextRequest) {
  let token = "";
  let paused = false;
  try {
    const b = await req.json();
    token = String(b?.token ?? "");
    paused = b?.paused === true;
  } catch {
    return NextResponse.json({ ok: false, error: "bad body" }, { status: 400 });
  }
  if (!token) {
    return NextResponse.json({ ok: false, error: "token required" }, { status: 400 });
  }
  const r = await pool.query(
    "UPDATE device_push_tokens SET paused = $2, updated_at = now() WHERE token = $1",
    [token, paused],
  );
  if (r.rowCount === 0) {
    return NextResponse.json({ ok: false, error: "unknown device" }, { status: 404 });
  }
  return NextResponse.json({ ok: true, paused });
}
