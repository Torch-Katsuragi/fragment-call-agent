import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { phonebookName } from "@/lib/phonebook";
import { setUrgentState, urgentState } from "@/lib/urgentCallers";

export const dynamic = "force-dynamic";

// 緊急呼び出しを許可する相手の管理。
// ⚠許可の根拠は**本人が明示的に承認したこと**だけ。番号の見た目や相手の名乗りは使わない
//   (理由は lib/urgentCallers.ts の冒頭コメント — 発信者番号は偽装され得る)。
//   ここが出すのは「候補」で、承認は人がする。

type Row = { number: string; name: string | null; last_at: string; reason: string; calls: number };

export async function GET() {
  let allowed: Row[] = [];
  let candidates: Row[] = [];
  try {
    // ⚠候補の根拠は**本人由来のものだけ**: 本人が通話に入った / 本人が発信した。
    //   相手が申告できる情報 (名乗り) は根拠にしない
    const r = await pool.query(
      `SELECT caller_number AS number,
              max(started_at) AS last_at,
              count(*)::int AS calls,
              bool_or(direction = 'outbound') AS called_out,
              bool_or(answered_by IN ('human', 'ai_then_human')) AS talked
         FROM calls
        WHERE caller_number ~ '^[0-9]{4,20}$'
          AND (direction = 'outbound' OR answered_by IN ('human', 'ai_then_human'))
        GROUP BY caller_number
        ORDER BY max(started_at) DESC
        LIMIT 60`,
    );
    for (const row of r.rows) {
      const state = urgentState(row.number);
      if (state === "denied") continue; // 一度断った相手は再提案しない
      const item: Row = {
        number: row.number,
        name: phonebookName(row.number),
        last_at: row.last_at,
        calls: row.calls,
        reason: row.talked
          ? row.called_out
            ? "本人が話した・発信もした"
            : "本人が通話に入った"
          : "本人が発信した",
      };
      (state === "allowed" ? allowed : candidates).push(item);
    }
  } catch {
    // DB不調時は空で返す (画面は「候補なし」になるだけ)
  }
  return NextResponse.json({ allowed, candidates });
}

export async function POST(req: NextRequest) {
  let number = "";
  let allow = false;
  try {
    const body = await req.json();
    number = String(body?.number ?? "");
    allow = body?.allow === true;
  } catch {
    return NextResponse.json({ ok: false, error: "bad body" }, { status: 400 });
  }
  try {
    setUrgentState(number, allow);
  } catch (e) {
    return NextResponse.json(
      { ok: false, error: e instanceof Error ? e.message : "failed" },
      { status: 400 },
    );
  }
  return NextResponse.json({ ok: true });
}
