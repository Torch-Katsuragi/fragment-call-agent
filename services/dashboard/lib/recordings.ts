import fs from "node:fs";
import path from "node:path";
import { FRAGMENT_WORKSPACE } from "./env";

// 通話の録音 (2026-10-02)。worker (services/directory-agent/recordings.py) が
// ワークスペースの 録音/YYYY-MM/<日時JST>_<番号>.ogg に置く。ここは読むだけ — 消すのも worker
// (管制室は agent_jobs に頼む)。書き手を 1 つにして、通話記録 md の埋め込みと食い違わせないため

export const REC_DIR = () => path.join(FRAGMENT_WORKSPACE, "録音");

/** calls.recording_path (ワークスペースからの相対) を実パスに。録音フォルダの外は拒む */
export function resolveRecording(rel: string): string | null {
  const root = path.resolve(REC_DIR());
  const p = path.resolve(FRAGMENT_WORKSPACE, rel);
  if (!p.startsWith(root + path.sep) || !p.endsWith(".ogg")) return null;
  return p;
}

export type RecFile = { date: string; bytes: number };

/** 録音の一覧 (日付 JST は名前の頭から取る) */
export function listRecordings(): RecFile[] {
  const out: RecFile[] = [];
  let months: string[] = [];
  try {
    months = fs.readdirSync(REC_DIR());
  } catch {
    return out;
  }
  for (const m of months) {
    let names: string[] = [];
    try {
      names = fs.readdirSync(path.join(REC_DIR(), m));
    } catch {
      continue;
    }
    for (const n of names) {
      const d = /^(\d{4}-\d{2}-\d{2})_.*\.ogg$/.exec(n);
      if (!d) continue;
      try {
        out.push({ date: d[1], bytes: fs.statSync(path.join(REC_DIR(), m, n)).size });
      } catch {
        /* 消えた直後 */
      }
    }
  }
  return out;
}
