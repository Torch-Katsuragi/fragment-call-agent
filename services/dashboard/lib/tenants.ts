import fs from "node:fs";
import path from "node:path";
import { Pool } from "pg";
import { DATABASE_URL } from "./env";

// テナント (管制室) の同居 (2026-10-04)。設計は Vault の 1_製品/同居構成_開発記録。
//
// 管制室はテナントごとに別プロセス (同じビルドをポートと環境変数を変えて起動) で、入口は
// fragment.example.com 1 つ。Caddy が Cookie `fr_tenant` と端末のトークンの頭 (fr1_<id>_) で振り分ける。
// ⚠このプロセスが扱う DB・作業フォルダ・hookd は環境変数のまま (= 自分のテナントだけ)。
//   他のテナントの DB に触るのは「この人はどのテナントのメンバーか」を引くときだけ (ログイン・招待・端末のログイン)

export type Tenant = {
  id: string;
  label: string;
  room_prefix: string;
  database: string;
  dashboard_port: number;
};

/** このプロセスのテナント。systemd のユニットで TENANT_ID を渡す (無ければ main = ユーザー本人の管制室) */
export const TENANT_ID = process.env.TENANT_ID || "main";
export const DEFAULT_TENANT = "main";
export const TENANT_COOKIE = "fr_tenant";

const FILE =
  process.env.TENANTS_FILE ?? path.resolve(process.cwd(), "..", "..", "infra", "tenants", "tenants.json");

export function listTenants(): Tenant[] {
  try {
    return JSON.parse(fs.readFileSync(FILE, "utf8")).tenants ?? [];
  } catch {
    return [];
  }
}

export function currentTenant(): Tenant | null {
  return listTenants().find((t) => t.id === TENANT_ID) ?? null;
}

/** 通話の部屋名の頭 (call / kumiai …)。このプロセスが扱ってよい部屋はこれで始まるものだけ */
export const ROOM_PREFIX = process.env.ROOM_PREFIX || currentTenant()?.room_prefix || "call";

export function ownsRoom(room: string): boolean {
  return room.startsWith(ROOM_PREFIX + "_");
}

/** 端末のトークンの頭。main は従来どおり fr1_ だけ (配布済みのトークンを無効にしない) */
export function deviceTokenPrefix(tenantId: string): string {
  return tenantId === DEFAULT_TENANT ? "fr1_" : `fr1_${tenantId}_`;
}

// 他のテナントの DB (メンバーを引くだけ)。接続はテナントごとに 1 本の小さなプール
const globalForPools = globalThis as unknown as { tenantPools?: Map<string, Pool> };
const pools = globalForPools.tenantPools ?? new Map<string, Pool>();
globalForPools.tenantPools = pools;

export function poolFor(t: Tenant): Pool {
  let p = pools.get(t.id);
  if (!p) {
    const url = DATABASE_URL.replace(/\/[^/]*$/, "/" + t.database);
    p = new Pool({ connectionString: url, max: 2 });
    pools.set(t.id, p);
  }
  return p;
}

/** このメールアドレスがメンバーになっているテナント (利用停止は除く) */
export async function tenantsOfEmail(email: string): Promise<Tenant[]> {
  const out: Tenant[] = [];
  for (const t of listTenants()) {
    try {
      const r = await poolFor(t).query(
        "SELECT 1 FROM members WHERE email = $1 AND status = 'active'",
        [email.toLowerCase()],
      );
      if (r.rowCount) out.push(t);
    } catch {
      /* そのテナントの DB がまだ無い */
    }
  }
  return out;
}

/**
 * 要求をそのテナントの管制室へそのまま渡して、答えを返す (アプリのログイン・招待からの参加)。
 * ⚠アプリは最初どのテナントか知らずに入口 (main) へ来る。Cookie を持たないので Caddy では振り分けられない
 */
export async function forwardTo(t: Tenant, pathname: string, body: unknown): Promise<Response> {
  const res = await fetch(`http://127.0.0.1:${t.dashboard_port}${pathname}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "x-forwarded-tenant-hop": "1" },
    body: JSON.stringify(body),
    cache: "no-store",
  });
  return new Response(await res.text(), {
    status: res.status,
    headers: { "Content-Type": res.headers.get("Content-Type") ?? "application/json" },
  });
}

/** 招待のトークンを持っているテナント */
export async function tenantOfInvite(tokenHash: string): Promise<Tenant | null> {
  for (const t of listTenants()) {
    try {
      const r = await poolFor(t).query("SELECT 1 FROM invites WHERE token_hash = $1", [tokenHash]);
      if (r.rowCount) return t;
    } catch {
      /* 同上 */
    }
  }
  return null;
}
