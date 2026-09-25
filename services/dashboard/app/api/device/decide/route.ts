import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const HOOKD = process.env.HOOKD_URL ?? "http://127.0.0.1:8790";

// 着信への答えのうち「出る」以外 (2026-09-25)。hookd の /ringing_decide へ中継するだけ。
//   ai     … 残りの呼び出しを待たずに AI に渡す
//   reject … 相手ごと切る (AI にも回さない)
// ⚠ダイヤルプランが /pickup_wait で待っているので、押された瞬間に中継すること
export async function POST(req: NextRequest) {
  let number = "";
  let action = "";
  try {
    const body = await req.json();
    number = String(body?.number ?? "");
    action = String(body?.action ?? "");
  } catch {
    return NextResponse.json({ ok: false, error: "bad body" }, { status: 400 });
  }
  if (!/^\+?[0-9]{4,20}$/.test(number) || !["ai", "reject"].includes(action)) {
    return NextResponse.json({ ok: false, error: "bad request" }, { status: 400 });
  }
  try {
    const q = `number=${encodeURIComponent(number)}&action=${action}`;
    const res = await fetch(`${HOOKD}/ringing_decide?${q}`, { cache: "no-store" });
    if (!res.ok) {
      // 鳴り終わった後 = もう次の段へ進んでいる
      return NextResponse.json({ ok: false, error: "not ringing" }, { status: 404 });
    }
  } catch {
    return NextResponse.json({ ok: false, error: "hookd unreachable" }, { status: 502 });
  }
  return NextResponse.json({ ok: true });
}
