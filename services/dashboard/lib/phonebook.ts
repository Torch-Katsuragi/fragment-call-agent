import fs from "node:fs";
import path from "node:path";
import { FRAGMENT_WORKSPACE } from "./env";

// 電話帳 (フラグメント/連絡先/)。2026-09-26 から番号ごとのフォルダ:
//   連絡先/<番号>/<番号>.md … 番号の基本情報 (持ち主の名前・メモ・番号検索・最近の用件)
//   連絡先/<番号>/<人>.md   … 組織の番号からかけてくる個人ごと
// 書き手の決まり・節の意味は services/directory-agent/phonebook.py の冒頭 (連絡先/連絡先.md にも同じもの)。
// ⚠管制室が書くのは名前・読みがな (スマホの電話帳から登録)・緊急呼び出しの許可だけ。ほかは worker
// ⚠旧形式 (連絡先/<番号>.md) も読む。書くときに新しい形へ移す
// dashboard はワークスペースを直接読み書きする (VM では rclone bisync で Drive と同期、5 分おき)。
// 名前の解決は 30 秒キャッシュ
const cache = new Map<string, { name: string | null; at: number }>();
const TTL = 30_000;

const ROOT = () => path.join(FRAGMENT_WORKSPACE, "連絡先");

export function isNumber(n: string | null | undefined): n is string {
  return !!n && /^[+0-9]{4,20}$/.test(n);
}

/** 読む場所。新しい形が無ければ旧形式。どちらも無ければ null */
export function contactFile(number: string): string | null {
  if (!isNumber(number)) return null;
  const neu = path.join(ROOT(), number, `${number}.md`);
  if (fs.existsSync(neu)) return neu;
  const old = path.join(ROOT(), `${number}.md`);
  return fs.existsSync(old) ? old : null;
}

/** 書く場所。旧形式があれば新しい形へ移す (機械の節の見出しに「（自動）」を付ける)。無ければ null */
export function contactFileForWrite(number: string): string {
  const neu = path.join(ROOT(), number, `${number}.md`);
  if (fs.existsSync(neu)) return neu;
  fs.mkdirSync(path.dirname(neu), { recursive: true });
  const old = path.join(ROOT(), `${number}.md`);
  if (fs.existsSync(old)) {
    let md = fs.readFileSync(old, "utf-8");
    for (const t of ["番号検索", "最近の用件", "話した人", "未整理", "食い違い"]) {
      md = md.replace(new RegExp(`^## ${t}[ \\t]*$`, "gm"), `## ${t}（自動）`);
    }
    fs.writeFileSync(neu, md, "utf-8");
    fs.unlinkSync(old);
  }
  return neu;
}

/** スマホの電話帳の番号 (+81 90-1234-5678 など) をファイル名の形 (09012345678) にそろえる */
export function normalizeNumber(raw: string): string | null {
  let n = raw.replace(/[\s\-().]/g, "");
  if (n.startsWith("+81")) n = "0" + n.slice(3);
  return isNumber(n) ? n : null;
}

function nameOf(md: string): string | null {
  const m = md.match(/^name:\s*["']?(.+?)["']?\s*$/m);
  const name = m ? m[1].trim() : null;
  return name === "不明" || name === "" ? null : name;
}

/** 読みがな (frontmatter の kana、ひらがな)。並べ替えと検索に使う (2026-09-26) */
function kanaOf(md: string): string | null {
  const m = md.match(/^kana:\s*["']?(.+?)["']?\s*$/m);
  return m && m[1].trim() ? m[1].trim() : null;
}

/** 「## <見出し>」の節の中身。機械の節は「（自動）」付きでも同じ節として読む */
function sectionOf(md: string, heading: string): string {
  const m = md.match(
    new RegExp(`^## ${heading}(?:（自動）)?[ \\t]*\\n([\\s\\S]*?)(?=^## |(?![\\s\\S]))`, "m"),
  );
  return (m ? m[1] : "").trim();
}

/** 「## メモ」の中身。雛形の書き込み前の文言は空として扱う */
function memoOf(md: string): string {
  const memo = sectionOf(md, "メモ");
  return memo.startsWith("(まだ情報なし") ? "" : memo;
}

export function phonebookName(number: string | null): string | null {
  if (!isNumber(number)) return null;
  const hit = cache.get(number);
  if (hit && Date.now() - hit.at < TTL) return hit.name;
  let name: string | null = null;
  try {
    const f = contactFile(number);
    if (f) name = nameOf(fs.readFileSync(f, "utf-8"));
  } catch {
    /* 電話帳mdなし = 初対面 */
  }
  cache.set(number, { name, at: Date.now() });
  return name;
}

// lookup = 「## 番号検索」の節 (worker が検索結果で書き、通話と食い違えば ⚠ で残す)
// people = 話した人 (組織の番号からかけてきた個人)。人のファイルから「### 名前」+ 用件の行で組み立てる
// unsorted = 「## 未整理」(同じ人か迷って振り分けなかった名乗り)
// conflicts = 「## 食い違い」(番号検索や登録済みの名前と、通話での名乗りが違った記録)
export type Contact = {
  number: string;
  name: string | null;
  kana: string | null;
  memo: string;
  lookup: string;
  people: string;
  unsorted: string;
  conflicts: string;
  /** 相手ごとの応答 (2026-09-30)。null = 回線の設定に従う */
  answer: ContactAnswer | null;
};

// 相手ごとの応答。判定は hookd (services/agent/schedule.py の contact_answer)。⚠値の文字列を揃えること
export const CONTACT_ANSWERS = ["人が出る", "AIが出る"] as const;
export type ContactAnswer = (typeof CONTACT_ANSWERS)[number];

function answerOf(md: string): ContactAnswer | null {
  const v = md.match(/^応答:\s*(\S+)\s*$/m)?.[1];
  return (CONTACT_ANSWERS as readonly string[]).includes(v ?? "") ? (v as ContactAnswer) : null;
}

/** 相手ごとの応答を書く。null で消す (回線の設定に従う)。⚠人が決める値 — 機械は書かない */
export function setContactAnswer(number: string, answer: ContactAnswer | null): void {
  const file = contactFile(number);
  if (!file) throw new Error("電話帳にない番号です");
  const md = fs.readFileSync(file, "utf-8");
  let next = md.replace(/^応答:.*\r?\n/m, "");
  if (answer) next = next.replace(/^---\r?\n/, `---\n応答: ${answer}\n`);
  if (next !== md) fs.writeFileSync(file, next, "utf-8");
  cache.delete(number);
}

function peopleOf(number: string): string {
  const dir = path.join(ROOT(), number);
  let files: string[] = [];
  try {
    files = fs.readdirSync(dir).filter((f) => f.endsWith(".md") && f !== `${number}.md`);
  } catch {
    return "";
  }
  return files
    .sort()
    .map((f) => {
      const md = fs.readFileSync(path.join(dir, f), "utf-8");
      const memo = sectionOf(md, "メモ");
      const lines = sectionOf(md, "用件");
      const note = memo && !memo.startsWith("(この人について") ? `${memo}\n` : "";
      return `### ${f.slice(0, -3)}\n${note}${lines}`;
    })
    .join("\n");
}

/** 電話帳の全件 (2026-09-26、アプリの電話帳タブ)。⚠`_未使用/` と `連絡先.md` (使い方の説明) は除く */
export function listPhonebook(): Contact[] {
  let entries: fs.Dirent[] = [];
  try {
    entries = fs.readdirSync(ROOT(), { withFileTypes: true });
  } catch {
    return [];
  }
  const numbers = new Set<string>();
  for (const e of entries) {
    if (e.isDirectory() && isNumber(e.name)) numbers.add(e.name);
    else if (e.isFile() && e.name.endsWith(".md") && isNumber(e.name.slice(0, -3))) numbers.add(e.name.slice(0, -3));
  }
  const out: Contact[] = [];
  for (const number of numbers) {
    try {
      const f = contactFile(number);
      if (!f) continue;
      const md = fs.readFileSync(f, "utf-8");
      out.push({
        number,
        name: nameOf(md),
        kana: kanaOf(md),
        memo: memoOf(md),
        lookup: sectionOf(md, "番号検索"),
        people: peopleOf(number),
        unsorted: sectionOf(md, "未整理"),
        conflicts: sectionOf(md, "食い違い"),
        answer: answerOf(md),
      });
    } catch {
      /* 読めないファイルは飛ばす */
    }
  }
  return out;
}

/**
 * 名前を登録する (スマホの電話帳から選んだ相手、2026-09-26)。
 * 無ければ雛形で作り、あれば name の行だけ書き換える — メモと機械の節は触らない。
 * ⚠本人が選んで登録した名前なので name_source (機械が付けた印) は外す — 以後 worker は名前を変えない
 * @return created = 新しく作ったか
 */
export function upsertContact(number: string, name: string, kana?: string | null): { created: boolean } {
  const existed = contactFile(number) !== null;
  const file = contactFileForWrite(number);
  const oneLine = (s: string) => s.replace(/[\r\n"]/g, " ").trim().slice(0, 60);
  const clean = oneLine(name);
  const k = kana ? oneLine(kana) : "";
  if (existed) {
    const md = fs.readFileSync(file, "utf-8");
    let next = /^name:.*$/m.test(md)
      ? md.replace(/^name:.*$/m, `name: ${clean}`)
      : md.replace(/^---\r?\n/, `---\nname: ${clean}\n`);
    next = next.replace(/^name_source:.*\r?\n/m, "");
    // 読みがなはスマホの連絡先に付いていたときだけ書く (無ければ既存のものを残す。
    // 無いままのものは worker が名前から補う)
    if (k) {
      next = /^kana:.*$/m.test(next)
        ? next.replace(/^kana:.*$/m, `kana: ${k}`)
        : next.replace(/^(name:.*)$/m, `$1\nkana: ${k}`);
    }
    if (next !== md) fs.writeFileSync(file, next, "utf-8");
  } else {
    fs.writeFileSync(
      file,
      `---\nnumber: "${number}"\nname: ${clean}\n${k ? `kana: ${k}\n` : ""}tags: [フラグメント, 電話帳]\n---\n\n# ${number}\n\n` +
        `## メモ\n\n(まだ情報なし。名前・関係・話し方の注意などをここに書く)\n\n## 最近の用件（自動）\n\n`,
      "utf-8",
    );
  }
  cache.delete(number);
  return { created: !existed };
}
