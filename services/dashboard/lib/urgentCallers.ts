import fs from "node:fs";
import path from "node:path";
import { FRAGMENT_WORKSPACE } from "./env";

// 「緊急呼び出しを許可した相手」の台帳。実体は電話帳md
// (フラグメント/連絡先/<番号>.md) の frontmatter にある `緊急呼び出し` プロパティ。
//
// ⚠**なぜ番号や名前で自動判定しないのか** (2026-08-01の調査で確定):
//   日本では発信者番号の偽装が現在進行形の主要手口で、警察庁が「表示された番号を
//   信用するな」と公式に注意喚起している (末尾0110の警察署番号を騙る詐欺、被害100億円超)。
//   米国のSTIR/SHAKENのような**着信側で検証する基盤が日本には無い**ため、
//   「番号が◯◯のものだから信用する」は原理的に成立しない。
//   しかも公的機関ほど騙りの標的になるので「信用できる番号ほど危ない」逆転が起きている。
//   ⚠この製品では騙りが成功すると**本人の携帯が鳴る** — 盾が騙る相手にだけ下りる。
//
//   したがって**許可は本人が明示的に与えたものだけ**を信用する。
//   機械がやるのは「候補を挙げる」ところまでで、承認が安全境界。
//
// ⚠候補の根拠も**本人由来のものだけ**を使う (相手の名乗りや番号の見た目は使わない):
//   ・本人が実際にその通話に入った (answered_by = human / ai_then_human)
//   ・本人がその番号へ発信した (direction = outbound)
//   これらは相手が申告できない。ただし**番号自体は偽装され得る**ので、
//   「過去に話した番号を騙る」標的型攻撃は理屈上は通る。マス詐欺には効かず、
//   かつ本人が一件ずつ承認するので、許容できる残余リスクと判断した。

export type UrgentState = "allowed" | "denied" | "unset";

const FLAG = "緊急呼び出し";

function mdPath(number: string): string {
  return path.join(FRAGMENT_WORKSPACE, "連絡先", `${number}.md`);
}

function isValid(number: string): boolean {
  return /^[0-9]{4,20}$/.test(number);
}

/** 電話帳mdから許可状態を読む。mdが無い/プロパティが無いなら unset */
export function urgentState(number: string): UrgentState {
  if (!isValid(number)) return "unset";
  try {
    const md = fs.readFileSync(mdPath(number), "utf-8");
    const m = md.match(new RegExp(`^${FLAG}:\\s*(\\S+)\\s*$`, "m"));
    if (!m) return "unset";
    return /^(true|yes|はい)$/i.test(m[1]) ? "allowed" : "denied";
  } catch {
    return "unset";
  }
}

/**
 * 許可状態を書き込む。⚠frontmatter が無いmdや、mdそのものが無い場合も作る
 * (電話帳mdは通話後にworkerが雛形を作るが、発信だけの相手には存在しないため)。
 */
export function setUrgentState(number: string, allowed: boolean): void {
  if (!isValid(number)) throw new Error("invalid number");
  const p = mdPath(number);
  const line = `${FLAG}: ${allowed}`;
  let md: string;
  try {
    md = fs.readFileSync(p, "utf-8");
  } catch {
    fs.mkdirSync(path.dirname(p), { recursive: true });
    md = `---\nnumber: "${number}"\nname: 不明\ntags: [フラグメント, 電話帳]\n---\n\n# ${number}\n`;
  }
  const re = new RegExp(`^${FLAG}:.*$`, "m");
  if (re.test(md)) {
    md = md.replace(re, line);
  } else if (md.startsWith("---\n")) {
    // frontmatterの閉じ `---` の直前に差し込む
    const end = md.indexOf("\n---", 4);
    md = end < 0 ? `---\n${line}\n${md.slice(4)}` : md.slice(0, end + 1) + line + "\n" + md.slice(end + 1);
  } else {
    md = `---\nnumber: "${number}"\n${line}\n---\n\n${md}`;
  }
  fs.writeFileSync(p, md, "utf-8");
}
