import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { extractDeviceToken } from "@/lib/deviceToken";
import { hashToken } from "@/lib/members";

export const dynamic = "force-dynamic";

// 端末の一時停止 (2026-09-24)。アプリの常駐通知・設定ページから呼ばれる。
// ⚠端末はこの要求のログインで決める (2026-10-04、端末の情報はログインの行だけ)。
//   以前は FCM トークンで見分けていた (アプリはまだ token を送ってくるが使わない)
// ⚠一時停止中の端末は hookd が「起きろ」を送らず、全端末が一時停止ならスタンバイの待ちを飛ばす
//   (hookd.pickup_wait)。端末側も自分で鳴らさない (二重の守り)
export async function POST(req: NextRequest) {
  const b = await req.json().catch(() => null);
  if (!b) return NextResponse.json({ ok: false, error: "bad body" }, { status: 400 });
  const paused = b.paused === true;
  const session = extractDeviceToken(req);
  if (!session) return NextResponse.json({ ok: false, error: "端末のログインがありません" }, { status: 401 });
  const r = await pool.query("UPDATE device_sessions SET paused = $2 WHERE token_hash = $1 AND revoked_at IS NULL", [
    hashToken(session),
    paused,
  ]);
  if (r.rowCount === 0) return NextResponse.json({ ok: false, error: "unknown device" }, { status: 404 });
  return NextResponse.json({ ok: true, paused });
}
