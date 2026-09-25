import { Pool } from "pg";
import { DATABASE_URL } from "./env";

// Next.js devサーバーのホットリロードでPoolが増殖しないよう globalThis にキャッシュ
const globalForPg = globalThis as unknown as { pgPool?: Pool };

export const pool =
  globalForPg.pgPool ?? new Pool({ connectionString: DATABASE_URL, max: 5 });

globalForPg.pgPool = pool;

export type CallRow = {
  id: string;
  caller_number: string | null;
  room_name: string;
  started_at: string;
  ended_at: string | null;
};

export type SegmentRow = {
  call_id: string;
  seq: number;
  speaker: string; // caller / ai / user
  text: string;
  at: string;
};
