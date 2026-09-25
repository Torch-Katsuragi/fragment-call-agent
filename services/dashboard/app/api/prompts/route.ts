import { NextRequest, NextResponse } from "next/server";
import fs from "node:fs/promises";
import path from "node:path";
import { FRAGMENT_WORKSPACE } from "@/lib/env";

export const dynamic = "force-dynamic";

// 応対プロンプトのセクション編集 (2026-07-30)。
// 実体は Google Drive と同期されるワークスペース内の md なので、管制室からでも
// Obsidianからでも直せる。agent は**通話ごとに読み直す**ので保存すれば次の通話から効く。
//
// セクションに切って合成する形にした理由: 「情報の扱い」と「人格と態度」は別の関心事で、
// 直す動機も頻度も違う。1枚の巨大プロンプトだと、口調を直したいだけのときに
// 開示ルールまで目に入って事故る。
// ⚠agent.py の PROMPT_SECTIONS と揃えること (ズレると編集先が食い違う)。
const SECTIONS = {
  disclosure: {
    title: "情報の扱い (開示ルール)",
    desc: "誰に何を教えないか。氏名・住所・予定の扱いと、番号違いのときの応対",
    default: "disclosure.md",
    workspace: path.join("設定", "情報の扱い.md"),
  },
  persona: {
    title: "人格と態度 (話し方・温度感)",
    desc: "口調・敬語の硬さ・1発話の長さ・愛想の温度",
    default: "persona.md",
    workspace: path.join("設定", "人格と態度.md"),
  },
  // ⚠これだけ性質が違う: 「設定」ではなく頻繁に変わる状態。判断には使うが相手には明かさない。
  //   ⚠2026-07-31時点では**音声アシスタントには渡していない** (裏の判断層の持ち物にした)。
  //     判断層=取り次ぎの実装がまだ無いので、いま編集しても通話の応対は変わらない。
  //     「編集したのに効かない」と誤解されないよう desc に明記してある
  owner_status: {
    title: "本人の今の状況 (相手には明かさない)",
    // ⚠保存できるだけで通話には効かない (取り次ぎ判断の実装待ち、音声アシスタントには渡していない)。
    //   画面では一言で伝える (2026-09-24 設定ページの整理)
    desc: "会議中・運転中など。⚠いまは通話に反映されません",
    default: "owner_status.md",
    workspace: path.join("設定", "本人の今の状況.md"),
  },
} as const;

type Key = keyof typeof SECTIONS;

const DEFAULT_DIR = path.resolve(process.cwd(), "..", "agent", "prompts");

const wsPath = (k: Key) => path.join(FRAGMENT_WORKSPACE, SECTIONS[k].workspace);
const defPath = (k: Key) => path.join(DEFAULT_DIR, SECTIONS[k].default);

async function readSection(k: Key) {
  const def = await fs.readFile(defPath(k), "utf-8");
  let text = def;
  let source: "workspace" | "default" = "default";
  try {
    text = await fs.readFile(wsPath(k), "utf-8");
    source = "workspace";
  } catch {
    // ワークスペースにまだ無い = 既定のまま動いている状態。作るのは保存時
  }
  return { key: k, ...SECTIONS[k], text, default: def, source, path: wsPath(k) };
}

export async function GET() {
  try {
    const keys = Object.keys(SECTIONS) as Key[];
    return NextResponse.json({ sections: await Promise.all(keys.map(readSection)) });
  } catch (e) {
    return NextResponse.json(
      { error: `既定のプロンプトが読めません (${DEFAULT_DIR}): ${e}` },
      { status: 500 },
    );
  }
}

function pick(body: unknown): Key | null {
  const k = (body as { key?: string } | null)?.key;
  return k && k in SECTIONS ? (k as Key) : null;
}

export async function PUT(req: NextRequest) {
  const body = await req.json().catch(() => null);
  const key = pick(body);
  if (!key) {
    return NextResponse.json(
      { error: `key は ${Object.keys(SECTIONS).join(" / ")} のいずれか` },
      { status: 400 },
    );
  }
  const text = (body as { text?: unknown }).text;
  if (typeof text !== "string" || !text.trim()) {
    return NextResponse.json({ error: "text (空でない文字列) が必要です" }, { status: 400 });
  }
  const p = wsPath(key);
  try {
    await fs.mkdir(path.dirname(p), { recursive: true });
    await fs.writeFile(p, text, "utf-8");
  } catch (e) {
    return NextResponse.json({ error: `保存できません (${p}): ${e}` }, { status: 500 });
  }
  return NextResponse.json(await readSection(key));
}

// 既定に戻す = ワークスペースのmdを既定の内容で**上書き**する。
// ⚠ファイルを消すのではなく上書きする — 消すとDrive同期で「削除」が伝播して
//   他端末側の編集内容まで消える可能性があるため
export async function DELETE(req: NextRequest) {
  const key = pick(await req.json().catch(() => null));
  if (!key) {
    return NextResponse.json(
      { error: `key は ${Object.keys(SECTIONS).join(" / ")} のいずれか` },
      { status: 400 },
    );
  }
  const p = wsPath(key);
  try {
    await fs.mkdir(path.dirname(p), { recursive: true });
    await fs.writeFile(p, await fs.readFile(defPath(key), "utf-8"), "utf-8");
  } catch (e) {
    return NextResponse.json({ error: `戻せません (${p}): ${e}` }, { status: 500 });
  }
  return NextResponse.json(await readSection(key));
}
