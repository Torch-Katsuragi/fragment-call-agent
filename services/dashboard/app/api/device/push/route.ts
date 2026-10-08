import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { extractDeviceToken } from "@/lib/deviceToken";
import { hashToken } from "@/lib/members";

export const dynamic = "force-dynamic";

// 端末が FCM トークンを登録する (2026-09-18)。hookd が着信・取り次ぎの瞬間にここへ「起きろ」を送る。
//
// ⚠端末トークン (認証) と FCM トークン (宛先) は別物。認証は middleware が済ませている
// ⚠トークンは端末の再インストールで変わる。古いものは hookd が UNREGISTERED を受けて消す
export async function POST(req: NextRequest) {
  let token = "";
  try {
    const b = await req.json();
    token = String(b?.token ?? "");
  } catch {
    return NextResponse.json({ ok: false, error: "bad body" }, { status: 400 });
  }
  if (!/^[A-Za-z0-9_:\-]{20,4096}$/.test(token)) {
    return NextResponse.json({ ok: false, error: "bad token" }, { status: 400 });
  }
  const session = extractDeviceToken(req);
  if (!session) return NextResponse.json({ ok: false, error: "端末のログインがありません" }, { status: 401 });
  // 宛先はこの端末のログインの行に置く (2026-10-04、端末の情報はログインの行だけ)。
  // 同じ宛先が別のログインに残っていたら外す (アカウントを替えた端末)
  await pool.query("UPDATE device_sessions SET push_token = NULL WHERE push_token = $1 AND token_hash <> $2", [
    token,
    hashToken(session),
  ]);
  await pool.query("UPDATE device_sessions SET push_token = $1 WHERE token_hash = $2", [token, hashToken(session)]);
  return NextResponse.json({ ok: true });
}

// 端末がプッシュをやめる
export async function DELETE(req: NextRequest) {
  const session = extractDeviceToken(req);
  if (!session) return NextResponse.json({ ok: false, error: "端末のログインがありません" }, { status: 401 });
  await pool.query("UPDATE device_sessions SET push_token = NULL WHERE token_hash = $1", [hashToken(session)]);
  return NextResponse.json({ ok: true });
}
