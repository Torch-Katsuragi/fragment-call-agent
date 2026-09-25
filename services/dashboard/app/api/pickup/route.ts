import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const HOOKD = process.env.HOOKD_URL ?? "http://127.0.0.1:8790";

// 受話ボタン: hookd に受話要求を出す → Asterisk のダイヤルプランが検知して LiveKit にブリッジ
export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  const number = typeof body?.number === "string" ? body.number : "";
  if (!/^[+0-9]{4,20}$/.test(number)) {
    return NextResponse.json({ ok: false, error: "bad number" }, { status: 400 });
  }
  try {
    const res = await fetch(
      `${HOOKD}/pickup_request?number=${encodeURIComponent(number)}`,
      { cache: "no-store" }
    );
    return NextResponse.json(await res.json(), { status: res.status });
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e) }, { status: 502 });
  }
}
