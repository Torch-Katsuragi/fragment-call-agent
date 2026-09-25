import { NextRequest, NextResponse } from "next/server";
import { AccessToken } from "livekit-server-sdk";
import { LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_PUBLIC_WS_URL } from "@/lib/env";

export const dynamic = "force-dynamic";

// M2: 認証なしのローカル専用トークン発行。OAuth + Orchestrator に置き換えるのは M4 (DESIGN §5.2)
export async function GET(req: NextRequest) {
  const room = req.nextUrl.searchParams.get("room");
  if (!room) {
    return NextResponse.json({ error: "room is required" }, { status: 400 });
  }

  // role=operator は交代 (マイク発話) 用。agent側は identity の operator- プレフィックスで検知する
  const isOperator = req.nextUrl.searchParams.get("role") === "operator";
  const identity = `${isOperator ? "operator" : "dashboard"}-${Math.random().toString(36).slice(2, 8)}`;
  const at = new AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET, {
    identity,
    ttl: "1h",
  });
  at.addGrant({
    roomJoin: true,
    room,
    canSubscribe: true,
    canPublish: isOperator,
    canPublishData: false,
  });

  // url はブラウザが繋ぐ先なので公開URL (HTTPS配信時は wss://) を返す。lib/env.ts の注意書き参照
  return NextResponse.json({ token: await at.toJwt(), url: LIVEKIT_PUBLIC_WS_URL, identity });
}
