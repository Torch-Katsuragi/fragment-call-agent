import { NextRequest, NextResponse } from "next/server";
import { pool, CallRow, SegmentRow } from "@/lib/db";
import { phonebookName } from "@/lib/phonebook";

export const dynamic = "force-dynamic";

// 通話1件の詳細 + 全文文字起こし
export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const call = await pool.query<CallRow>(
      `SELECT id, caller_number, room_name, started_at, ended_at, direction
       FROM calls WHERE id = $1`,
      [id],
    );
    if (call.rows.length === 0) {
      return NextResponse.json({ error: "not found" }, { status: 404 });
    }
    const segments = await pool.query<SegmentRow>(
      `SELECT call_id, seq, speaker, text, at FROM transcript_segments
       WHERE call_id = $1 ORDER BY id`,
      [id],
    );
    const fragments = await pool.query(
      `SELECT id, kind, title, text, created_at FROM fragments WHERE call_id = $1 ORDER BY id`,
      [id],
    );
    return NextResponse.json({
      ...call.rows[0],
      caller_name: phonebookName(call.rows[0].caller_number),
      segments: segments.rows,
      fragments: fragments.rows,
    });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 502 });
  }
}
