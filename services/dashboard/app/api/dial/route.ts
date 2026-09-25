import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";

const HOOKD = process.env.HOOKD_URL ?? "http://127.0.0.1:8790";

// 発信: hookd /dial → AMI Originate → 相手が応答したら outbound-bridge が LiveKit へ。
// ブラステル経由の実発信 = 通話料がかかる (確認ダイアログはUI側)
export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  const number =
    typeof body?.number === "string" ? body.number.replace(/[-\s()]/g, "") : "";
  if (!/^\+?[0-9]{4,20}$/.test(number)) {
    return NextResponse.json({ ok: false, error: "番号の形式が不正です" }, { status: 400 });
  }
  try {
    const res = await fetch(`${HOOKD}/dial?number=${encodeURIComponent(number)}`, {
      cache: "no-store",
    });
    return NextResponse.json(await res.json(), { status: res.status });
  } catch (e) {
    return NextResponse.json({ ok: false, error: String(e) }, { status: 502 });
  }
}

// 発信状態 (発信中バナーがポーリングする)
export async function GET() {
  try {
    const res = await fetch(`${HOOKD}/dial_state`, { cache: "no-store" });
    return NextResponse.json(await res.json(), { status: res.status });
  } catch {
    return NextResponse.json({ dialing: null });
  }
}
