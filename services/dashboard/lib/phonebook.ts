import fs from "node:fs";
import path from "node:path";
import { FRAGMENT_WORKSPACE } from "./env";

// 電話帳md (フラグメント/連絡先/<番号>.md) の name プロパティを解決する。
// dashboardはホストで動いているのでGoogle DriveのG:を直接読める。30秒キャッシュ
const cache = new Map<string, { name: string | null; at: number }>();
const TTL = 30_000;

export function phonebookName(number: string | null): string | null {
  if (!number || !/^[+0-9]{4,20}$/.test(number)) return null;
  const hit = cache.get(number);
  if (hit && Date.now() - hit.at < TTL) return hit.name;
  let name: string | null = null;
  try {
    const md = fs.readFileSync(
      path.join(FRAGMENT_WORKSPACE, "連絡先", `${number}.md`),
      "utf-8",
    );
    const m = md.match(/^name:\s*["']?(.+?)["']?\s*$/m);
    if (m) name = m[1].trim();
    if (name === "不明" || name === "") name = null;
  } catch {
    /* 電話帳mdなし = 初対面 */
  }
  cache.set(number, { name, at: Date.now() });
  return name;
}
