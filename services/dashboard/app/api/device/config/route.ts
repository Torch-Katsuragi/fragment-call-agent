import { NextResponse } from "next/server";
import { fcmClientConfig, LINES } from "@/lib/env";

export const dynamic = "force-dynamic";

// 端末向けの接続設定 (2026-09-18)。Firebase (FCM) の接続情報と、フラグメントが受けている番号。
//
// ⚠ペアリング済みの端末が後から取りに来る口。/api/device/pair にも同じものを載せてあるが、
//   サーバー側で FCM を有効にした時に既存の端末が再ペアリング無しで拾えるようにここにも置く。
//   firebase が null ならアプリはプッシュ無し (ロングポーリングのみ) で動く
// ⚠lines (2026-09-25): アプリの設定画面の「番号」に並べる。管制室の「回線」と同じ出どころ (lib/env の LINES)。
//   設定済みの回線だけ — 検証用に置いてあるだけの回線は出さない
export async function GET() {
  return NextResponse.json({
    firebase: fcmClientConfig(),
    lines: LINES.filter((l) => l.configured).map((l) => ({
      id: l.id,
      label: l.label,
      number: l.number,
      role: l.role,
    })),
  });
}
