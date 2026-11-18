import { NextRequest, NextResponse } from "next/server";
import { normalizeNumber, saveContactDetails } from "@/lib/phonebook";

export const dynamic = "force-dynamic";

// POST … 管制室の電話帳画面から 1 件の新規登録または編集 (2026-11-14)。
//   権限は middleware.ts (path.startsWith("/api/phonebook") && write → admin 以上) で判定済み。
//   触るのは frontmatter の name / kana と「## メモ」節のみ (機械の「（自動）」節は触らない)。
export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  const number = typeof body?.number === "string" ? normalizeNumber(body.number) : null;
  if (!number) {
    return NextResponse.json({ error: "電話番号の形が正しくありません" }, { status: 400 });
  }
  const name = typeof body?.name === "string" ? body.name : undefined;
  const kana = typeof body?.kana === "string" ? body.kana : undefined;
  const memo = typeof body?.memo === "string" ? body.memo : undefined;
  if (name === undefined && kana === undefined && memo === undefined) {
    return NextResponse.json({ error: "変更内容がありません" }, { status: 400 });
  }
  try {
    const res = saveContactDetails(number, { name, kana, memo });
    return NextResponse.json({ ok: true, number, created: res.created });
  } catch (e) {
    return NextResponse.json(
      { error: String((e as Error).message ?? e) },
      { status: 500 },
    );
  }
}
