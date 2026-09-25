import { NextRequest, NextResponse } from "next/server";
import { RoomServiceClient } from "livekit-server-sdk";
import { pool } from "@/lib/db";
import { LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_WS_URL } from "@/lib/env";

export const dynamic = "force-dynamic";

// 通話終了ボタン: LiveKitのルームを削除する → SIP側にBYEが流れて回線ごと切れる
export async function POST(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  try {
    const r = await pool.query(
      "SELECT room_name, ended_at FROM calls WHERE id = $1",
      [id],
    );
    if (r.rows.length === 0) {
      return NextResponse.json({ error: "not found" }, { status: 404 });
    }
    if (r.rows[0].ended_at) {
      return NextResponse.json({ ok: true, already_ended: true });
    }
    const svc = new RoomServiceClient(
      LIVEKIT_WS_URL.replace(/^ws/, "http"),
      LIVEKIT_API_KEY,
      LIVEKIT_API_SECRET,
    );
    await svc.deleteRoom(r.rows[0].room_name);
    return NextResponse.json({ ok: true });
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 502 });
  }
}
