import { NextRequest, NextResponse } from "next/server";
import { pool } from "@/lib/db";
import { phonebookName } from "@/lib/phonebook";
import { numberLookup, type NumberLookup } from "@/lib/numberLookup";
import { deriveAnswerMode } from "@/lib/answerMode";

export const dynamic = "force-dynamic";
// ロングポーリングで最大25秒ぶら下がる。⚠既定 (Vercel等の短いタイムアウト) では切られるので
// セルフホストの前提。切られても害は無い (アプリは即座に張り直す)
export const maxDuration = 60;

const HOOKD = process.env.HOOKD_URL ?? "http://127.0.0.1:8790";

// スマホアプリ (専用電話アプリ) が叩く、端末用のまとめ状態。
//
// ⚠ブラウザ用の /api/ringing とは別に立ててある。アプリが要るのは
//   ①取り次ぎの呼び出し ②着信 (スタンバイの2秒鳴動) ③通話中カードの有無 ④応答モード
//   だけで、これを個別に叩くと往復が増えるうえ、画面都合の変更に引きずられるため。
//
// ⚠**ロングポーリング対応** (2026-08-01)。`?wait=<秒>&v=<前回のv>` を付けると、
//   状態が変わるか wait 秒経つまで返さない。スタンバイの猶予が2秒しかないので、
//   1.5秒間隔のポーリングでは**猶予をまるごと取りこぼす**のが理由。
//   wait 無しの従来どおりの単発GETも動く (v1のアプリと互換)。
export async function GET(req: NextRequest) {
  const waitSec = Math.min(Number(req.nextUrl.searchParams.get("wait") ?? 0) || 0, 25);
  const since = req.nextUrl.searchParams.get("v") ?? "";
  const deadline = Date.now() + waitSec * 1000;

  for (;;) {
    const snap = await snapshot();
    if (snap.v !== since || Date.now() >= deadline) {
      return NextResponse.json(snap);
    }
    // ⚠hookd はローカル。300msごとの再問い合わせで足りる (猶予2秒に対して十分細かい)
    await new Promise((r) => setTimeout(r, 300));
  }
}

async function snapshot() {
  // --- ①取り次ぎ (hookd の in-memory 状態) ---
  let handoff: {
    room: string;
    number: string;
    name: string | null;
    reason: string;
    expires_in: number;
    fragments: { kind: string; title: string; text: string }[];
    recent: { speaker: string; text: string }[];
    lookup: NumberLookup | null;
  } | null = null;
  try {
    const res = await fetch(`${HOOKD}/handoff_state`, { cache: "no-store" });
    if (res.ok) {
      const h = (await res.json()).handoff;
      if (h) {
        // 呼び出し中に本人が見る判断材料。⚠どちらも既にDBにあるので追加のLLM呼び出しは無い。
        //   鳴っている間も相手はAIと話し続けているため、アプリのポーリングで中身が伸びる
        //   (「迷っている間に材料が増える」のがこの画面の要点 — 2026-08-01の設計)
        // ⚠fragments は【本人限定】情報を含みうる。**ロック画面に出すかの判断は端末側**で行う
        let fragments: { kind: string; title: string; text: string }[] = [];
        let recent: { speaker: string; text: string }[] = [];
        try {
          const f = await pool.query(
            `SELECT kind, title, text FROM fragments
             WHERE call_id = (SELECT id FROM calls WHERE room_name = $1 ORDER BY started_at DESC LIMIT 1)
             ORDER BY id DESC LIMIT 4`,
            [h.room],
          );
          fragments = f.rows.map((r) => ({
            kind: r.kind ?? "info",
            title: r.title ?? "",
            text: r.text ?? "",
          }));
          const s = await pool.query(
            `SELECT speaker, text FROM transcript_segments
             WHERE call_id = (SELECT id FROM calls WHERE room_name = $1 ORDER BY started_at DESC LIMIT 1)
             ORDER BY id DESC LIMIT 6`,
            [h.room],
          );
          recent = s.rows.reverse().map((r) => ({ speaker: r.speaker, text: r.text }));
        } catch {
          // 材料が取れなくても呼び出しは鳴らす (名前と理由だけで判断できる)
        }
        const name = phonebookName(h.number ?? null);
        handoff = {
          room: h.room,
          number: h.number ?? "",
          name,
          reason: h.reason ?? "",
          expires_in: h.expires_in ?? 0,
          fragments,
          recent,
          // 電話帳に無い相手だけ。web検索の名前で「番号検索中…」を置き換える
          lookup: name ? null : await numberLookup(h.number ?? null),
        };
      }
    }
  } catch {
    // hookd停止中は呼び出し無し扱い。⚠鳴らさない方に倒す (誤って鳴るより無音の方がまし)
  }

  // --- ②着信 (スタンバイの2秒鳴動 / 自分で出るの呼び出し) ---
  // ⚠取り次ぎ (①) とは別物。こちらは**まだAIが出ていない着信**を本人が先に取る経路。
  //   鳴らす秒数はサーバーが決める (ring_ms) — 端末が勝手に鳴らし続けると、
  //   ダイヤルプランが既にAIへ進んだ後も鳴っていて「取れない着信」になる
  let incoming: {
    number: string;
    name: string | null;
    mode: string;
    ring_ms: number;
    // announcing = 録音告知中。相手は出すが、出るボタンと着信音はまだ (2026-09-25)
    phase: "announcing" | "ringing";
    lookup: NumberLookup | null;
  } | null = null;
  try {
    const res = await fetch(`${HOOKD}/ringing_state`, { cache: "no-store" });
    if (res.ok) {
      const r = (await res.json()).ringing;
      if (r && !r.pickup && (r.ring_ms ?? 0) > 0) {
        const name = phonebookName(r.number ?? null);
        incoming = {
          number: r.number ?? "",
          name,
          mode: r.mode ?? "away",
          ring_ms: r.ring_ms,
          phase: r.phase === "announcing" ? "announcing" : "ringing",
          // ⚠鳴り始めはまず pending。検索が終わると指紋が変わってロングポーリングが返り、
          //   端末の着信画面が名前に置き換わる
          lookup: name ? null : await numberLookup(r.number ?? null),
        };
      }
    }
  } catch {
    // hookd停止中は鳴らさない (誤って鳴るより無音の方がまし)
  }

  // --- ③通話中 (ロック画面の「通話中」カード用) ---
  // id も返す — アプリが応答後に /call/<id>?op=1 を開くのに要る (room からは引けない)
  let activeCall: { id: string; room: string; number: string; name: string | null } | null = null;
  try {
    const r = await pool.query(
      `SELECT id, room_name, caller_number FROM calls
       WHERE ended_at IS NULL
       ORDER BY started_at DESC LIMIT 1`,
    );
    if (r.rows.length > 0) {
      const number = r.rows[0].caller_number ?? "";
      activeCall = {
        id: String(r.rows[0].id),
        room: r.rows[0].room_name,
        number,
        name: phonebookName(number || null),
      };
    }
  } catch {
    // DB不調時はカードを出さない
  }

  // --- ③' 終話 (2026-09-25) ---
  // AI が応対して終わった着信を、要約つきで返す。端末はこれを「こういう通話でした」の通知にする
  // (同じ id は端末が一度しか出さない)。
  // ⚠1件だけ返していた頃は、続けて2本終わると先の1本が通知されなかった。直近の数件を返し、
  //   どれを出すかは端末が覚えている通知済みの id で決める⚠要約は worker が終話後に作る (calls.summary_md)。
  //   できるまでは返さない — 本文の無い通知を先に出して後から差し替えるより、数秒遅れる方がよい。
  //   空文字は「作れなかった」の印で、そのときは本文なしで出す
  const recentEnded: {
    id: string;
    number: string;
    name: string | null;
    summary: string;
    duration_sec: number;
    ended_at: string;
  }[] = [];
  try {
    const r = await pool.query(
      `SELECT id, caller_number, summary_md, ended_at,
              extract(epoch from ended_at - started_at)::int AS dur
       FROM calls
       WHERE ended_at > now() - interval '10 minutes' AND summary_md IS NOT NULL
         AND direction = 'inbound' AND answered_by = 'ai'
       ORDER BY ended_at DESC LIMIT 5`,
    );
    for (const c of r.rows) {
      const number = c.caller_number ?? "";
      recentEnded.push({
        id: String(c.id),
        number,
        name: phonebookName(number || null) ?? (await numberLookup(number || null))?.name ?? null,
        summary: c.summary_md ?? "",
        duration_sec: c.dur ?? 0,
        ended_at: new Date(c.ended_at).toISOString(),
      });
    }
  } catch {
    // DB不調時は出さない
  }

  // --- ④応答モード ---
  let answerMode = "away";
  try {
    const r = await pool.query(
      "SELECT key, value FROM settings WHERE key IN ('answer_mode', 'assistant_enabled')",
    );
    answerMode = deriveAnswerMode(new Map(r.rows.map((x) => [x.key, x.value])));
  } catch {
    // DB不調時はAI応答 (fail-open) と揃える
  }

  // ロングポーリングの変化検知に使う指紋。⚠**時間で変わる値を入れないこと** —
  //   ring_ms や expires_in を混ぜると毎回変わってロングポーリングが成立しない
  //   逆に、呼び出し中に**中身が伸びる**のはこの画面の要点なので、
  //   吹き出しと会話の件数は指紋に混ぜる (増えたら即座に返す)
  const v = [
    handoff?.room ?? "-",
    handoff ? `${handoff.fragments.length}/${handoff.recent.length}` : "-",
    incoming ? `${incoming.number}:${incoming.mode}:${incoming.phase}` : "-",
    // 検索が終わったら返す (着信画面の「番号検索中…」を置き換えるため)
    `${incoming?.lookup?.status ?? "-"}/${handoff?.lookup?.status ?? "-"}`,
    activeCall?.id ?? "-",
    recentEnded.map((e) => e.id).join(",") || "-",
    answerMode,
  ].join("|");

  return {
    v,
    handoff,
    incoming,
    active_call: activeCall,
    recent_ended: recentEnded,
    answer_mode: answerMode,
  };
}
