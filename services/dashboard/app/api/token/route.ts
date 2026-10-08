import { NextRequest, NextResponse } from "next/server";
import { AccessToken } from "livekit-server-sdk";
import { LIVEKIT_API_KEY, LIVEKIT_API_SECRET, LIVEKIT_PUBLIC_WS_URL } from "@/lib/env";
import { deviceFrom } from "@/lib/device";
import { atLeast } from "@/lib/roles";
import { ownsRoom } from "@/lib/tenants";

export const dynamic = "force-dynamic";

// M2: 認証なしのローカル専用トークン発行。OAuth + Orchestrator に置き換えるのは M4 (DESIGN §5.2)
//
// role=operator は会話 (マイク発話) 用。agent は identity の operator- で「会話中の人」を数え、
// 担い手 (ai / human / hold) を決める (services/agent/presence.py)。
// ⚠identity の形 (2026-09-26):
//   アプリ   … 会話 `operator-<端末id>` / 視聴 `watch-<端末id>`、name = 端末の名前
//   管制室   … 会話 `operator-pc<乱数>` / 視聴 `dashboard-<乱数>`、name = 管制室
//   同じ端末が入り直すと LiveKit が前の接続を追い出すので、端末の二重参加は起きない
export async function GET(req: NextRequest) {
  const room = req.nextUrl.searchParams.get("room");
  if (!room) {
    return NextResponse.json({ error: "room is required" }, { status: 400 });
  }
  // ⚠他のテナントの通話には入らせない (2026-10-04、同居構成)。LiveKit は全テナント共有なので、
  //   ここで止めないと部屋名さえ分かれば他の管制室の通話を聴けてしまう
  if (!ownsRoom(room)) {
    return NextResponse.json({ error: "この管制室の通話ではありません" }, { status: 403 });
  }

  const isOperator = req.nextUrl.searchParams.get("role") === "operator";
  // 会話 (マイクを出す) は応対以上 (2026-09-26、lib/roles.ts)。閲覧の人は視聴だけ。
  // ⚠開発 (next dev) では middleware がヘッダを付けないのでここも通す
  const role = req.headers.get("x-member-role");
  if (isOperator && role !== null && !atLeast(role, "responder")) {
    return NextResponse.json({ error: "会話に入る権限がありません" }, { status: 403 });
  }
  const dev = deviceFrom(req);
  const rand = Math.random().toString(36).slice(2, 8);
  const identity = dev
    ? `${isOperator ? "operator" : "watch"}-${dev.id}`
    : isOperator
      ? `operator-pc${rand}`
      : `dashboard-${rand}`;
  // アプリも PC の管制室もメンバーの名前で出す (2026-10-04)。「管制室が応対中」では誰か分からない
  const memberName = decodeURIComponent(req.headers.get("x-member-name") ?? "").slice(0, 40);
  const at = new AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET, {
    identity,
    // 表示名はメンバーの名前 (端末は名前を持たない、2026-10-04)
    name: memberName || (dev ? "端末" : "管制室"),
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
