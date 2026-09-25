import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const HOOKD = process.env.HOOKD_URL ?? "http://127.0.0.1:8790";

// 端末が取り次ぎに応答した。hookd の /handoff_accept へ中継するだけ。
//
// ⚠ここを叩いた瞬間に、AI側の request_handoff ツールが「つながりました」を受け取って黙る。
//   だから**本人が本当に出たときだけ**呼ぶこと (画面を開いただけでは呼ばない)。
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
    const res = await fetch(`${HOOKD}/handoff_accept?room=${encodeURIComponent(room)}`, {
      cache: "no-store",
    });
    if (!res.ok) {
      // 期限切れ・他の受け手が先に取った
      return NextResponse.json({ ok: false, error: "not ringing" }, { status: 404 });
    }
  } catch {
    return NextResponse.json({ ok: false, error: "hookd unreachable" }, { status: 502 });
  }
  return NextResponse.json({ ok: true });
}
