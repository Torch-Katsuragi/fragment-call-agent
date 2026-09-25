import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const HOOKD = process.env.HOOKD_URL ?? "http://127.0.0.1:8790";

// 端末が着信に出た (スタンバイの2秒鳴動 / 自分で出る)。hookd の /pickup_request へ中継するだけ。
//
// ⚠取り次ぎの /api/device/handoff/accept とは別物。あちらは**AIが応対中の通話**に
//   本人が入る経路で、こちらは**まだAIが出ていない着信**を本人が先に取る経路。
//   混ぜると、AIが出た後に「本人が先に取った」ことになって二重に音声が乗る。
//
// ⚠スタンバイでは猶予が2秒しかない。ダイヤルプランが /pickup_wait で待っているので、
//   ここは押された瞬間に中継すること (キューに積んで後で流す、をしない)。
export async function POST(req: NextRequest) {
  let number = "";
  try {
    number = String((await req.json())?.number ?? "");
  } catch {
    return NextResponse.json({ ok: false, error: "bad body" }, { status: 400 });
  }
  if (!/^\+?[0-9]{4,20}$/.test(number)) {
    return NextResponse.json({ ok: false, error: "bad number" }, { status: 400 });
  }
  try {
    const res = await fetch(`${HOOKD}/pickup_request?number=${encodeURIComponent(number)}`, {
      cache: "no-store",
    });
    if (!res.ok) {
      // 鳴り終わった後 = もうAIに渡っている。端末側は鳴り止ませる
      return NextResponse.json({ ok: false, error: "not ringing" }, { status: 404 });
    }
  } catch {
    return NextResponse.json({ ok: false, error: "hookd unreachable" }, { status: 502 });
  }
  return NextResponse.json({ ok: true });
}
