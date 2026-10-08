import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { ANSWER_MODES, isAnswerMode, aiAnswers, modeInfo, setModeByHand } from "@/lib/answerMode";

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
      WHERE key IN ('answer_mode', 'assistant_enabled', 'tts_primary', 'tts_fallback', 'self_label', 'staff',
                    'recording_retention_days')`,
  );
  const got = new Map<string, string>(r.rows.map((x) => [x.key, x.value]));
  // ⚠いまのモードは hookd に聞く (時間割・手動の上書き。lib/answerMode.ts の modeInfo)
  const info = await modeInfo(async () => got);
  const mode = info.mode;
  return NextResponse.json({
    answer_mode: mode,
    mode_info: info,
    answer_modes: ANSWER_MODES,
    assistant_enabled: aiAnswers(mode),
    // 未設定は空文字で返す = 「環境変数の既定に任せる」の意味
    tts_primary: got.get("tts_primary") ?? "",
    tts_fallback: got.get("tts_fallback") ?? "",
    tts_choices: TTS_CHOICES,
    // この電話の名乗り (2026-09-30)。空 = 名乗らない。AI の挨拶の頭に付く
    self_label: got.get("self_label") ?? "",
    // 名前を出してよい人 (苗字と役職、2026-09-30)。AI が話の流れで「担当の山田課長」のように出す
    staff: parseStaff(got.get("staff")),
    // 録音の保存日数 (2026-10-02)。0 = 自動では消さない。消すのは worker (recordings.py)
    recording_retention_days: Number(got.get("recording_retention_days") || 0),
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
    // 時間割が有効なら次の切り替わりまでの上書きになる (hookd)。落ちていれば直に書く
    const info = await setModeByHand(m);
    if (!info) {
      await put("answer_mode", m);
      // 古い2値も揃える (着信バナー等がまだこちらを見ている)
      await put("assistant_enabled", String(aiAnswers(m)));
    }
    changed.answer_mode = m;
    if (info) changed.mode_info = info;
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

  // 名乗り。⚠挨拶は割り込み無効で流すので短く (20 文字まで)
  if ("self_label" in body) {
    const v = String(body.self_label ?? "").replace(/\s+/g, " ").trim();
    if (v.length > 20) return NextResponse.json({ error: "名乗りは20文字までにしてください" }, { status: 400 });
    await put("self_label", v);
    changed.self_label = v;
  }

  if ("staff" in body) {
    if (!Array.isArray(body.staff)) return NextResponse.json({ error: "staff は配列です" }, { status: 400 });
    const staff = parseStaff(JSON.stringify(body.staff));
    await put("staff", JSON.stringify(staff));
    changed.staff = staff;
  }

  // 録音を自動で消すまでの日数。0 = 消さない (既定)。⚠録音は取り返しがつかないので既定は消さない
  if ("recording_retention_days" in body) {
    const v = Number(body.recording_retention_days);
    if (!Number.isInteger(v) || v < 0 || v > 3650) {
      return NextResponse.json({ error: "保存日数は 0〜3650 の整数です" }, { status: 400 });
    }
    await put("recording_retention_days", String(v));
    changed.recording_retention_days = v;
  }

  if (Object.keys(changed).length === 0) {
    return NextResponse.json({ error: "変更する項目がありません" }, { status: 400 });
  }
  return NextResponse.json(changed);
}

type Staff = { surname: string; title: string };

/** 苗字と役職の一覧。⚠苗字だけを渡す (下の名前は入れない)。空の苗字は捨てる */
function parseStaff(raw: string | undefined): Staff[] {
  try {
    const v = JSON.parse(raw ?? "[]");
    if (!Array.isArray(v)) return [];
    return v
      .map((s) => ({
        surname: String(s?.surname ?? "").replace(/\s+/g, "").slice(0, 10),
        title: String(s?.title ?? "").replace(/\s+/g, " ").trim().slice(0, 20),
      }))
      .filter((s) => s.surname)
      .slice(0, 30);
  } catch {
    return [];
  }
}
