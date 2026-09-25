import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { ANSWER_MODES, deriveAnswerMode, isAnswerMode, aiAnswers } from "@/lib/answerMode";

// 管制室の設定 (settingsテーブル)。
// - answer_mode: 応答モード away / standby / manual (2026-08-01に2状態→3状態)。
//   ダイヤルプランが hookd /answer_mode 経由で参照する。
//   ⚠assistant_enabled も同時に書く — 古い2値を見ている経路 (着信バナー等) との整合のため。
//     読むときは answer_mode が正、無いときだけ assistant_enabled から導出する
// - assistant_enabled: 留守電ON/OFF (answer_mode の派生値。standby も ON 側)
// - tts_primary / tts_fallback: 声のメイン/サブ (2026-07-30追加)。
//   agent が**通話ごとに**読むので、切り替えは次の通話から効く (コンテナ再起動は不要)。
//   ⚠サブを置く理由: 外部APIに声を預けると落ちたときに電話が無言になる。
//     通話で一番痛い障害なので、合成が失敗したらフレームワークが次に切り替える

// 選択肢は agent.py の TTS_CHOICES と揃えること (ズレると保存できても効かない)
const TTS_CHOICES = ["aivis", "google", "gemini", "elevenlabs", "voicevox"];

export async function GET() {
  const r = await pool.query(
    `SELECT key, value FROM settings
      WHERE key IN ('answer_mode', 'assistant_enabled', 'tts_primary', 'tts_fallback')`,
  );
  const got = new Map<string, string>(r.rows.map((x) => [x.key, x.value]));
  const mode = deriveAnswerMode(got);
  return NextResponse.json({
    answer_mode: mode,
    answer_modes: ANSWER_MODES,
    assistant_enabled: aiAnswers(mode),
    // 未設定は空文字で返す = 「環境変数の既定に任せる」の意味
    tts_primary: got.get("tts_primary") ?? "",
    tts_fallback: got.get("tts_fallback") ?? "",
    tts_choices: TTS_CHOICES,
  });
}

async function put(key: string, value: string) {
  await pool.query(
    `INSERT INTO settings (key, value) VALUES ($1, $2)
     ON CONFLICT (key) DO UPDATE SET value = $2, updated_at = now()`,
    [key, value],
  );
}

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => null);
  if (!body || typeof body !== "object") {
    return NextResponse.json({ error: "JSONが必要です" }, { status: 400 });
  }

  const changed: Record<string, unknown> = {};

  if ("answer_mode" in body) {
    const m = String(body.answer_mode ?? "");
    if (!isAnswerMode(m)) {
      return NextResponse.json(
        { error: `answer_mode が不正です (${ANSWER_MODES.join(" / ")} のいずれか)` },
        { status: 400 },
      );
    }
    await put("answer_mode", m);
    // 古い2値も揃える (着信バナー等がまだこちらを見ている)
    await put("assistant_enabled", String(aiAnswers(m)));
    changed.answer_mode = m;
  } else if ("assistant_enabled" in body) {
    // 旧API互換。⚠standby からトグルすると away/manual に潰れる —
    //   3状態を扱う画面は answer_mode を送ること
    if (typeof body.assistant_enabled !== "boolean") {
      return NextResponse.json(
        { error: "assistant_enabled は boolean で指定してください" },
        { status: 400 },
      );
    }
    await put("assistant_enabled", String(body.assistant_enabled));
    await put("answer_mode", body.assistant_enabled ? "away" : "manual");
    changed.assistant_enabled = body.assistant_enabled;
  }

  // 声のメイン/サブ。"" は「環境変数の既定に任せる」、"none" はサブ無しの意味
  for (const key of ["tts_primary", "tts_fallback"] as const) {
    if (!(key in body)) continue;
    const v = String(body[key] ?? "");
    const allowed = v === "" || (key === "tts_fallback" && v === "none") || TTS_CHOICES.includes(v);
    if (!allowed) {
      return NextResponse.json(
        { error: `${key} が不正です (${TTS_CHOICES.join(" / ")} のいずれか)` },
        { status: 400 },
      );
    }
    await put(key, v);
    changed[key] = v;
  }

  if (Object.keys(changed).length === 0) {
    return NextResponse.json({ error: "変更する項目がありません" }, { status: 400 });
  }
  return NextResponse.json(changed);
}
