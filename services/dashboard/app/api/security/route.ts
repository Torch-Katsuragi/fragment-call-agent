import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";

export const dynamic = "force-dynamic";

// 警備 (SIP不正利用の検知) の一覧・確認・ブロックリスト操作。
// 検知そのものは hookd と agent (services/agent/security.py) が行い、ここは表示と手動操作のみ。
// テーブルは security.ensure_schema() が作る = hookd 起動前は存在しないので、
// 見つからない場合は「まだ何も無い」として空を返す (管制室が落ちない方が大事)
const NO_TABLE = "42P01";

export async function GET(req: NextRequest) {
  const limit = Math.min(Number(req.nextUrl.searchParams.get("limit") ?? 50), 200);
  try {
    const events = await pool.query(
      `SELECT id, first_at, last_at, count, kind, severity, source, action,
              caller_number, callee_number, detail, acknowledged
       FROM security_events ORDER BY last_at DESC LIMIT $1`,
      [limit],
    );
    const unacked = await pool.query(
      `SELECT count(*)::int AS n,
              bool_or(severity = 'critical') AS critical
       FROM security_events WHERE NOT acknowledged AND last_at > now() - interval '7 days'`,
    );
    const blocked = await pool.query(
      `SELECT number, reason, blocked_until, hits, created_at FROM blocked_callers
       WHERE blocked_until IS NULL OR blocked_until > now()
       ORDER BY created_at DESC LIMIT 100`,
    );
    const attempts = await pool.query(
      `SELECT count(*) FILTER (WHERE verdict <> 'allow')::int AS rejected,
              count(*)::int AS total
       FROM call_attempts WHERE at > now() - interval '24 hours'`,
    );
    // ブロック候補: 実際に着信のあった番号を出す。手入力させると打ち間違いで
    // 「ブロックしたつもりが効いていない」が起きるので、原則ここから選ばせる
    const candidates = await pool.query(
      `WITH nums AS (
         SELECT caller_number AS number, max(started_at) AS last_at,
                count(*)::int AS calls, NULL::text AS kind
           FROM calls WHERE coalesce(caller_number, '') <> '' GROUP BY 1
         UNION ALL
         SELECT caller_number, max(last_at), 0, max(kind)
           FROM security_events WHERE coalesce(caller_number, '') <> '' GROUP BY 1
         UNION ALL
         SELECT caller_number, max(at), 0, NULL
           FROM call_attempts WHERE coalesce(caller_number, '') <> '' GROUP BY 1
       )
       SELECT n.number, max(n.last_at) AS last_at, sum(n.calls)::int AS calls,
              max(n.kind) AS kind
         FROM nums n
        WHERE NOT EXISTS (
                SELECT 1 FROM blocked_callers b
                 WHERE b.number = n.number
                   AND (b.blocked_until IS NULL OR b.blocked_until > now()))
        GROUP BY n.number
        ORDER BY max(n.last_at) DESC NULLS LAST
        LIMIT 40`,
    );
    return NextResponse.json({
      events: events.rows,
      unacked: unacked.rows[0]?.n ?? 0,
      critical: unacked.rows[0]?.critical ?? false,
      blocked: blocked.rows,
      last24h: attempts.rows[0] ?? { rejected: 0, total: 0 },
      candidates: candidates.rows,
    });
  } catch (e: unknown) {
    if ((e as { code?: string })?.code === NO_TABLE) {
      return NextResponse.json({
        events: [],
        unacked: 0,
        critical: false,
        blocked: [],
        last24h: { rejected: 0, total: 0 },
        candidates: [],
      });
    }
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  if (!body) return NextResponse.json({ error: "JSONが必要です" }, { status: 400 });
  try {
    if (body.ack === "all") {
      await pool.query("UPDATE security_events SET acknowledged = true WHERE NOT acknowledged");
      return NextResponse.json({ ok: true });
    }
    if (typeof body.ack === "number") {
      await pool.query("UPDATE security_events SET acknowledged = true WHERE id = $1", [body.ack]);
      return NextResponse.json({ ok: true });
    }
    // 手動ブロックは恒久 (blocked_until = NULL)。自動ブロックは時限なので区別がつく
    if (typeof body.block === "string" && body.block.trim()) {
      const number = body.block.replace(/[^\d+]/g, "");
      if (!number) return NextResponse.json({ error: "番号が不正です" }, { status: 400 });
      await pool.query(
        `INSERT INTO blocked_callers (number, reason, blocked_until)
         VALUES ($1, $2, NULL)
         ON CONFLICT (number) DO UPDATE SET blocked_until = NULL, reason = EXCLUDED.reason`,
        [number, String(body.reason ?? "管制室から手動でブロック")],
      );
      return NextResponse.json({ ok: true, number });
    }
    if (typeof body.unblock === "string") {
      await pool.query("DELETE FROM blocked_callers WHERE number = $1", [body.unblock]);
      return NextResponse.json({ ok: true });
    }
    return NextResponse.json({ error: "ack / block / unblock のいずれかが必要です" }, { status: 400 });
  } catch (e: unknown) {
    if ((e as { code?: string })?.code === NO_TABLE) {
      return NextResponse.json({ error: "警備テーブル未作成 (hookd未起動)" }, { status: 503 });
    }
    return NextResponse.json({ error: String(e) }, { status: 500 });
  }
}
