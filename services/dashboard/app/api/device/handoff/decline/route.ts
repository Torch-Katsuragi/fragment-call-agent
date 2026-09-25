import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const HOOKD = process.env.HOOKD_URL ?? "http://127.0.0.1:8790";

// 取り次ぎに「AIに続けさせる」と答えた (2026-09-25)。hookd の /handoff_decline へ中継するだけ。
// ⚠これが無かった頃は端末が鳴り止むだけで、AI は時間切れ (25秒) まで相手を待たせていた
export async function POST(req: NextRequest) {
  let room = "";
  try {
    room = (await req.json())?.room ?? "";
  } catch {
    return NextResponse.json({ ok: false, error: "bad body" }, { status: 400 });
  }
  if (!room) {
    return NextResponse.json({ ok: false, error: "room is required" }, { status: 400 });
  }
  try {
    const res = await fetch(`${HOOKD}/handoff_decline?room=${encodeURIComponent(room)}`, {
      cache: "no-store",
    });
    if (!res.ok) {
      return NextResponse.json({ ok: false, error: "not ringing" }, { status: 404 });
    }
  } catch {
    return NextResponse.json({ ok: false, error: "hookd unreachable" }, { status: 502 });
  }
  return NextResponse.json({ ok: true });
}
