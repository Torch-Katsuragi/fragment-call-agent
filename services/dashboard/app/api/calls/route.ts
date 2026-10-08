import { NextRequest, NextResponse } from "next/server";
import { pool, CallRow, SegmentRow } from "@/lib/db";
import { phonebookName } from "@/lib/phonebook";

export const dynamic = "force-dynamic";

// 通話一覧 + 各通話の直近セグメント (カードのプレビュー用) + 要約。
// ⚠before (ISO 時刻) を付けると、それより前の通話を返す (2026-09-26、アプリのホームの無限スクロール)
export async function GET(req: NextRequest) {
  const limit = Math.min(Number(req.nextUrl.searchParams.get("limit") ?? 50), 200);
  const beforeRaw = req.nextUrl.searchParams.get("before");
  const before = beforeRaw && !Number.isNaN(Date.parse(beforeRaw)) ? new Date(beforeRaw) : null;
  try {
    const calls = await pool.query<
      CallRow & { summary_md: string | null; caller_label: string | null; handled_by: string[] | null }
    >(
      `SELECT id, caller_number, room_name, started_at, ended_at, direction, summary_md, caller_label, handled_by
       FROM calls
       WHERE ($2::timestamptz IS NULL OR started_at < $2)
       ORDER BY started_at DESC LIMIT $1`,
      [limit, before],
    );
    const ids = calls.rows.map((r) => r.id);
    let previews: SegmentRow[] = [];
    if (ids.length > 0) {
      const res = await pool.query<SegmentRow>(
        `SELECT call_id, seq, speaker, text, at FROM (
           SELECT ts.*, row_number() OVER (PARTITION BY call_id ORDER BY id DESC) AS rn
           FROM transcript_segments ts WHERE call_id = ANY($1::uuid[])
         ) t WHERE rn <= 3 ORDER BY call_id, id`,
        [ids],
      );
      previews = res.rows;
    }
    const byCall = new Map<string, SegmentRow[]>();
    for (const s of previews) {
      const arr = byCall.get(s.call_id) ?? [];
      arr.push(s);
      byCall.set(s.call_id, arr);
    }
    // 電話帳に無い番号は、番号検索の結果の名前 (事業者名など) を出す。
    // ⚠lib/numberLookup は着信中用で直近 24 時間しか見ない。履歴は古い通話も並ぶので、ここでまとめて引く
    const unknown = [
      ...new Set(
        calls.rows
          .map((c) => c.caller_number)
          .filter((n): n is string => !!n && !phonebookName(n)),
      ),
    ];
    const looked = new Map<string, string>();
    if (unknown.length > 0) {
      try {
        const r = await pool.query(
          `SELECT DISTINCT ON (query) query, result FROM agent_jobs
           WHERE kind = 'number_lookup' AND status = 'done' AND query = ANY($1::text[])
           ORDER BY query, id DESC`,
          [unknown],
        );
        for (const row of r.rows) {
          try {
            const name = JSON.parse(row.result)?.name;
            if (typeof name === "string" && name) looked.set(row.query, name);
          } catch {
            /* 壊れた結果は名前なし */
          }
        }
      } catch {
        /* 名前が引けなくても一覧は返す */
      }
    }
    return NextResponse.json(
      calls.rows.map((c) => ({
        ...c,
        caller_name: phonebookName(c.caller_number),
        lookup_name: c.caller_number ? (looked.get(c.caller_number) ?? null) : null,
        active: c.ended_at === null,
        preview: byCall.get(c.id) ?? [],
      })),
    );
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 502 });
  }
}
