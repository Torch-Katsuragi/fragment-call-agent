import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { listPhonebook, normalizeNumber, upsertContact } from "@/lib/phonebook";

export const dynamic = "force-dynamic";

// 電話帳 (2026-09-26、アプリの電話帳タブ)。実体はワークスペースの 連絡先/<番号>.md (lib/phonebook.ts)。
// GET  … 全件 + 通話の回数と最後の通話 (名前のある相手が先、あとは最後の通話が新しい順)
// POST … スマホの電話帳から選んだ相手を登録する {contacts: [{name, kana?, numbers: [...]}]}
export async function GET() {
  // ⚠初めての相手からの着信で worker が雛形を自動で作る (名前「不明」・メモ空)。
  //   それは電話帳というより通話の跡なので出さない。名前かメモのどちらかが入ったら出る
  const contacts = listPhonebook().filter((c) => c.name || c.memo);
  const stats = new Map<string, { count: number; last: string; lastId: string }>();
  if (contacts.length > 0) {
    try {
      const r = await pool.query(
        `SELECT DISTINCT ON (caller_number) caller_number, id, started_at,
                count(*) OVER (PARTITION BY caller_number) AS n
         FROM calls WHERE caller_number = ANY($1::text[])
         ORDER BY caller_number, started_at DESC`,
        [contacts.map((c) => c.number)],
      );
      for (const row of r.rows) {
        stats.set(row.caller_number, {
          count: Number(row.n),
          last: new Date(row.started_at).toISOString(),
          lastId: String(row.id),
        });
      }
    } catch {
      /* 通話の集計が取れなくても電話帳は返す */
    }
  }
  const out = contacts.map((c) => ({
    ...c,
    call_count: stats.get(c.number)?.count ?? 0,
    last_call_at: stats.get(c.number)?.last ?? null,
    last_call_id: stats.get(c.number)?.lastId ?? null,
  }));
  // 名前のある相手を読みがなのあいうえお順で先に (読みが無ければ名前そのもので)。名前の無い番号は後ろ
  out.sort((a, b) => {
    if (!!a.name !== !!b.name) return a.name ? -1 : 1;
    if (a.name && b.name) return (a.kana ?? a.name).localeCompare(b.kana ?? b.name, "ja");
    return (b.last_call_at ?? "").localeCompare(a.last_call_at ?? "");
  });
  return NextResponse.json({ contacts: out });
}

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  const list: { name?: unknown; kana?: unknown; numbers?: unknown }[] = Array.isArray(body?.contacts)
    ? body.contacts
    : [];
  const results: { number: string; name: string; created: boolean }[] = [];
  const skipped: string[] = [];
  for (const c of list.slice(0, 50)) {
    const name = typeof c.name === "string" ? c.name.trim() : "";
    const kana = typeof c.kana === "string" ? c.kana.trim() : "";
    const numbers = Array.isArray(c.numbers) ? c.numbers : [];
    for (const raw of numbers) {
      const n = typeof raw === "string" ? normalizeNumber(raw) : null;
      if (!n || !name) {
        skipped.push(String(raw));
        continue;
      }
      try {
        results.push({ number: n, name, ...upsertContact(n, name, kana) });
      } catch (e) {
        return NextResponse.json({ error: String(e), saved: results }, { status: 500 });
      }
    }
  }
  return NextResponse.json({ saved: results, skipped });
}

