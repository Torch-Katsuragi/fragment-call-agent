import fs from "node:fs";
import { Readable } from "node:stream";
import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { resolveRecording } from "@/lib/recordings";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

// 通話の録音を返す (2026-10-02)。<audio> の再生位置の移動に要るので Range に応える。
// 左=相手・右=こちら (AI か本人) のステレオ Ogg Opus
export async function GET(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const r = await pool.query<{ recording_path: string | null }>(
    "SELECT recording_path FROM calls WHERE id = $1",
    [id],
  );
  const rel = r.rows[0]?.recording_path;
  const file = rel ? resolveRecording(rel) : null;
  if (!file) return NextResponse.json({ error: "録音がありません" }, { status: 404 });
  let size: number;
  try {
    size = fs.statSync(file).size;
  } catch {
    return NextResponse.json({ error: "録音が見つかりません (消された可能性があります)" }, { status: 404 });
  }

  const headers: Record<string, string> = {
    "Content-Type": "audio/ogg",
    "Accept-Ranges": "bytes",
    "Cache-Control": "private, no-store",
  };
  const m = /^bytes=(\d*)-(\d*)$/.exec(req.headers.get("range") ?? "");
  if (m && (m[1] || m[2])) {
    let start = m[1] ? Number(m[1]) : size - Number(m[2]);
    let end = m[1] && m[2] ? Number(m[2]) : size - 1;
    start = Math.max(0, start);
    end = Math.min(end, size - 1);
    if (start > end) {
      return new NextResponse(null, { status: 416, headers: { "Content-Range": `bytes */${size}` } });
    }
    const body = Readable.toWeb(fs.createReadStream(file, { start, end })) as ReadableStream;
    return new NextResponse(body, {
      status: 206,
      headers: { ...headers, "Content-Range": `bytes ${start}-${end}/${size}`, "Content-Length": String(end - start + 1) },
    });
  }
  const body = Readable.toWeb(fs.createReadStream(file)) as ReadableStream;
  return new NextResponse(body, { headers: { ...headers, "Content-Length": String(size) } });
}
