import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { deviceFrom } from "@/lib/device";

export const dynamic = "force-dynamic";

const HOOKD = process.env.HOOKD_URL ?? "http://127.0.0.1:8790";

// 保留中に相手を指定して呼ぶ (2026-09-26)。宛先の端末で取り次ぎと同じ呼び出しが鳴り、
// 出れば会話に入る = 保留が解ける。hookd の /handoff_request (manual=1) へ中継する。
// targets が空なら全端末
export async function POST(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const body = await req.json().catch(() => null);
  // 候補は人単位で、id はその人の端末 id をカンマでつないだもの (2026-10-04、hookd presence.candidates)
  const targets: string[] = Array.isArray(body?.targets)
    ? body.targets
        .flatMap((t: unknown) => (typeof t === "string" ? t.split(",") : []))
        .filter((t: string) => /^[A-Za-z0-9-]{1,64}$/.test(t))
    : [];
  const r = await pool.query(
    "SELECT room_name, caller_number FROM calls WHERE id = $1 AND ended_at IS NULL",
    [id],
  );
  if (r.rows.length === 0) {
    return NextResponse.json({ error: "通話中ではありません" }, { status: 409 });
  }
  const dev = deviceFrom(req);
  const q = new URLSearchParams({
    room: r.rows[0].room_name,
    number: r.rows[0].caller_number ?? "",
    reason: "保留中の通話",
    manual: "1",
    by: dev?.name || "管制室",
    targets: targets.join(","),
  });
  try {
    const res = await fetch(`${HOOKD}/handoff_request?${q}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`hookd ${res.status}`);
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 502 });
  }
  return NextResponse.json({ ok: true });
}
