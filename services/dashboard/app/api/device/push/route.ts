import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { extractDeviceToken, verifyDeviceToken } from "@/lib/deviceToken";

export const dynamic = "force-dynamic";

// 端末が FCM トークンを登録する (2026-09-18)。hookd が着信・取り次ぎの瞬間にここへ「起きろ」を送る。
//
// ⚠端末トークン (認証) と FCM トークン (宛先) は別物。認証は middleware が済ませている。
//   ここでは端末トークンの中の名前 (n) を表示用に控えるだけで、認可には使わない
// ⚠トークンは端末の再インストールで変わる。古いものは hookd が UNREGISTERED を受けて消す
export async function POST(req: NextRequest) {
  let token = "";
  let platform = "android";
  try {
    const b = await req.json();
    token = String(b?.token ?? "");
    platform = String(b?.platform ?? "android").slice(0, 16);
  } catch {
    return NextResponse.json({ ok: false, error: "bad body" }, { status: 400 });
  }
  if (!/^[A-Za-z0-9_:\-]{20,4096}$/.test(token)) {
    return NextResponse.json({ ok: false, error: "bad token" }, { status: 400 });
  }
  const name = (await verifyDeviceToken(extractDeviceToken(req)))?.n ?? "";
  await pool.query(
    `INSERT INTO device_push_tokens (token, name, platform) VALUES ($1, $2, $3)
     ON CONFLICT (token) DO UPDATE SET name = $2, platform = $3, updated_at = now()`,
    [token, name.slice(0, 40), platform],
  );
  return NextResponse.json({ ok: true });
}

// 端末がプッシュをやめる (ペアリング解除)。
export async function DELETE(req: NextRequest) {
  let token = "";
  try {
    token = String((await req.json())?.token ?? "");
  } catch {
    return NextResponse.json({ ok: false, error: "bad body" }, { status: 400 });
  }
  await pool.query("DELETE FROM device_push_tokens WHERE token = $1", [token]);
  return NextResponse.json({ ok: true });
}
