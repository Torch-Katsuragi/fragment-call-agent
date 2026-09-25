import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";

export const dynamic = "force-dynamic";

// AIへの耳打ち: 会話ストリーム (transcript_segments) に speaker='whisper' で直接挿入する。
// 会話ストリームがバス — UIは即表示、watcherは文脈として購読、agentは新着を検知して
// アシスタントの会話履歴 (chat_ctx) に差し込む。相手には聞こえない。
// seqはagent側のメモリ採番と衝突しないよう +1000 の下駄を履かせる (並び順はidが正)
export async function POST(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const body = await req.json().catch(() => null);
  const text = typeof body?.text === "string" ? body.text.trim() : "";
  if (!text || text.length > 500) {
    return NextResponse.json({ error: "text (1〜500文字) が必要です" }, { status: 400 });
  }
  try {
    const r = await pool.query("SELECT ended_at FROM calls WHERE id = $1", [id]);
    if (r.rows.length === 0) {
      return NextResponse.json({ error: "not found" }, { status: 404 });
    }
    if (r.rows[0].ended_at) {
      return NextResponse.json({ error: "通話は終了しています" }, { status: 409 });
    }
    await pool.query(
      `INSERT INTO transcript_segments (call_id, seq, speaker, text)
       VALUES ($1,
               (SELECT COALESCE(MAX(seq), 0) + 1000
                FROM transcript_segments WHERE call_id = $1),
               'whisper', $2)`,
      [id, text],
    );
    // agent へ即時配達 (LISTEN agent_push) — 2秒ポーリング待ちを潰す
    await pool.query("SELECT pg_notify('agent_push', $1)", [id]);
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 502 });
  }
}
