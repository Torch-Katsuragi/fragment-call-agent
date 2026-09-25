import { NextRequest, NextResponse } from "next/server";
import { pool, CallRow, SegmentRow } from "@/lib/db";
import { phonebookName } from "@/lib/phonebook";

export const dynamic = "force-dynamic";

// 通話一覧 + 各通話の直近セグメント (カードのプレビュー用)
export async function GET(req: NextRequest) {
  const limit = Math.min(Number(req.nextUrl.searchParams.get("limit") ?? 50), 200);
  try {
    const calls = await pool.query<CallRow>(
      `SELECT id, caller_number, room_name, started_at, ended_at, direction
       FROM calls ORDER BY started_at DESC LIMIT $1`,
      [limit],
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
    return NextResponse.json(
      calls.rows.map((c) => ({
        ...c,
        caller_name: phonebookName(c.caller_number),
        active: c.ended_at === null,
        preview: byCall.get(c.id) ?? [],
      })),
    );
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 502 });
  }
}
