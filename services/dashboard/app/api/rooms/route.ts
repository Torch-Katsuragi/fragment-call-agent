import { NextResponse } from "next/server";
import { RoomServiceClient } from "livekit-server-sdk";
import { LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_HTTP_URL } from "@/lib/env";

export const dynamic = "force-dynamic";

export async function GET() {
  const svc = new RoomServiceClient(LIVEKIT_HTTP_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET);
  try {
    const rooms = await svc.listRooms();
    return NextResponse.json(
      rooms.map((r) => ({
        name: r.name,
        numParticipants: r.numParticipants,
        creationTime: Number(r.creationTime) * 1000,
      })),
    );
  } catch (e) {
    // LiveKit 未起動時もUIを壊さない (空一覧 + メッセージ)
    return NextResponse.json({ error: String(e) }, { status: 502 });
  }
}
