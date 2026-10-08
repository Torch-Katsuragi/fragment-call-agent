import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { extractDeviceToken } from "@/lib/deviceToken";
import { forget, hashToken } from "@/lib/members";

export const dynamic = "force-dynamic";

// アプリのログアウト (2026-10-04)。この端末のログインを切る (起こす宛先・呼び出し候補からも外れる)。
// ⚠アプリは「ログインし直す」「別のアカウントでログイン」の前に、古いログインのままこれを呼ぶ。
//   呼ばずに切り替えると、前の管制室に端末の登録 (FCM) が残り、「出られる端末」として数えられ続けた
export async function POST(req: NextRequest) {
  const token = extractDeviceToken(req);
  if (!token) return NextResponse.json({ error: "端末のログインがありません" }, { status: 400 });
  // 起こす宛先もこの行にあるので、一緒に消す (2026-10-04、端末の情報はログインの行だけ)
  await pool.query(
    "UPDATE device_sessions SET revoked_at = now(), push_token = NULL WHERE token_hash = $1 AND revoked_at IS NULL",
    [hashToken(token)],
  );
  forget();
  return NextResponse.json({ ok: true });
}
