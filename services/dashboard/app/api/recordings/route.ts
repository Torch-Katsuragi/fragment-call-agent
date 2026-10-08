import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { listRecordings } from "@/lib/recordings";

export const dynamic = "force-dynamic";

const DAY = /^\d{4}-\d{2}-\d{2}$/;

// 録音の件数と容量。?from=&to= (YYYY-MM-DD、JST、両端含む) を付けるとその期間の件数も返す。
// ?job=<id> は「期間を指定して消す」の進み具合
export async function GET(req: NextRequest) {
  const q = req.nextUrl.searchParams;
  const job = q.get("job");
  if (job) {
    const r = await pool.query("SELECT status, result FROM agent_jobs WHERE id = $1 AND kind = 'recording_delete'", [
      job,
    ]);
    if (r.rows.length === 0) return NextResponse.json({ error: "not found" }, { status: 404 });
    const { status, result } = r.rows[0];
    let deleted: number | null = null;
    try {
      deleted = status === "done" ? JSON.parse(result).deleted : null;
    } catch {
      /* 失敗時は result がエラー文 */
    }
    return NextResponse.json({ status, deleted, error: status === "error" ? result : null });
  }
  const all = listRecordings();
  const from = q.get("from") ?? "";
  const to = q.get("to") ?? "";
  const inRange = all.filter((f) => (!from || f.date >= from) && (!to || f.date <= to));
  return NextResponse.json({
    count: all.length,
    bytes: all.reduce((s, f) => s + f.bytes, 0),
    oldest: all.length ? all.map((f) => f.date).sort()[0] : null,
    range_count: from || to ? inRange.length : null,
  });
}

// 期間を指定して消す。実際に消すのは worker (agent_jobs の recording_delete)。
// ⚠消すのは録音だけ。文字起こしと通話記録 md は残る (md の埋め込みは worker が外す)
export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  const from = String(body?.from ?? "");
  const to = String(body?.to ?? "");
  if ((from && !DAY.test(from)) || (to && !DAY.test(to))) {
    return NextResponse.json({ error: "日付は YYYY-MM-DD で指定してください" }, { status: 400 });
  }
  if (!from && !to) {
    return NextResponse.json({ error: "期間を指定してください" }, { status: 400 });
  }
  if (from && to && from > to) {
    return NextResponse.json({ error: "開始日が終了日より後です" }, { status: 400 });
  }
  const r = await pool.query(
    "INSERT INTO agent_jobs (kind, query) VALUES ('recording_delete', $1) RETURNING id",
    [JSON.stringify({ from: from || null, to: to || null })],
  );
  return NextResponse.json({ job: r.rows[0].id });
}
