import { pool } from "./db";

// 番号のweb検索 (worker の number_lookup ジョブ) の進み具合。着信画面の「番号検索中…」用。
// ⚠pending は「ジョブがあって未完了」。ジョブ自体が無い (非通知・hookd停止) ときは null を返し、
//   端末は検索中を出さない — 終わらない「検索中」を出すよりまし
export type NumberLookup = {
  status: "pending" | "done";
  name: string | null;
  verdict: string;
  summary: string;
};

export async function numberLookup(number: string | null): Promise<NumberLookup | null> {
  if (!number || !/^[+0-9]{4,20}$/.test(number)) return null;
  try {
    const r = await pool.query(
      `SELECT status, result FROM agent_jobs
       WHERE kind = 'number_lookup' AND query = $1
         AND created_at > now() - interval '24 hours'
       ORDER BY (status = 'done') DESC, id DESC LIMIT 1`,
      [number],
    );
    if (r.rows.length === 0) return null;
    const row = r.rows[0];
    if (row.status === "pending" || row.status === "running") {
      return { status: "pending", name: null, verdict: "unknown", summary: "" };
    }
    if (row.status !== "done" || !row.result) return null;
    const j = JSON.parse(row.result);
    return {
      status: "done",
      name: typeof j.name === "string" && j.name ? j.name : null,
      verdict: j.verdict ?? "unknown",
      summary: j.summary ?? "",
    };
  } catch {
    return null;
  }
}
