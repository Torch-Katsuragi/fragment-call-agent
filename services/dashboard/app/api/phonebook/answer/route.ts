import { NextRequest, NextResponse } from "next/server";
import { CONTACT_ANSWERS, normalizeNumber, setContactAnswer, type ContactAnswer } from "@/lib/phonebook";

export const dynamic = "force-dynamic";

// POST … 相手ごとの応答 (2026-09-30) {number, answer: "人が出る" | "AIが出る" | null}。
//   ⚠POST にしたのはアプリの HttpURLConnection が PATCH を送れないため。
//   null = 回線の設定に従う。次の着信から効く (hookd が着信のたびに電話帳を読む)
export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  const number = typeof body?.number === "string" ? normalizeNumber(body.number) : null;
  const answer = body?.answer ?? null;
  if (!number) return NextResponse.json({ error: "番号が不正です" }, { status: 400 });
  if (answer !== null && !(CONTACT_ANSWERS as readonly string[]).includes(answer)) {
    return NextResponse.json({ error: `answer は ${CONTACT_ANSWERS.join(" / ")} か null` }, { status: 400 });
  }
  try {
    setContactAnswer(number, answer as ContactAnswer | null);
  } catch (e) {
    return NextResponse.json({ error: String((e as Error).message ?? e) }, { status: 404 });
  }
  return NextResponse.json({ number, answer });
}
