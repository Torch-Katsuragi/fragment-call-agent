// メンバー・招待・端末のログイン (2026-09-26)。表は services/agent/members.py。
//
// 誰かを決める経路は2つ:
//   ・管制室 (ブラウザ) … Google ログイン (next-auth) のメールアドレス
//   ・アプリ           … 端末ごとのログイン (device_sessions)。アプリで Google ログインして受け取る
// ⚠旧式のペアリング (QR で配っていた合言葉) は 2026-09-26 にやめた (ユーザー「後方互換は完全に絶っていい」)
// ⚠ここは Node で動く (middleware も runtime: nodejs)。DB を毎回見るので締め出しは次のリクエストから効く
import crypto from "node:crypto";
import { createRemoteJWKSet, jwtVerify } from "jose";
import { pool } from "./db";
import { atLeast, isRole, type Role } from "./roles";
import { TENANT_ID, deviceTokenPrefix } from "./tenants";

export type Member = { id: number; email: string; name: string; role: Role; status: string };
export type Who = { member: Member; deviceId: string | null; via: "web" | "device" | "dev" };

const DEVICE_PREFIX = "fr1_";

export function hashToken(t: string): string {
  return crypto.createHash("sha256").update(t).digest("hex");
}

function newToken(prefix: string): string {
  return prefix + crypto.randomBytes(32).toString("base64url");
}

function toMember(r: Record<string, unknown>): Member {
  return {
    id: Number(r.id),
    email: String(r.email),
    name: String(r.name ?? ""),
    role: isRole(r.role) ? r.role : "viewer",
    status: String(r.status ?? "active"),
  };
}

// ---- 最初のオーナー ----
// ⚠ALLOWED_EMAILS (それまでの「入ってよいアカウント」) を最初のオーナーにする。表が空でも誰かが入れるように
const BOOTSTRAP = (process.env.ALLOWED_EMAILS ?? "")
  .split(",")
  .map((e) => e.trim().toLowerCase())
  .filter(Boolean);
let bootstrapped = false;

export async function ensureOwners(): Promise<void> {
  if (bootstrapped) return;
  // ⚠メンバー表が空のときだけ (2026-10-04)。以前は起動のたびに入れ直していたので、テナントの共用アカウントに
  //   引き渡したあとも ALLOWED_EMAILS の個人アカウント (ユーザー) が外せなかった
  const n = await pool.query("SELECT 1 FROM members LIMIT 1").catch(() => null);
  if (n && n.rowCount) {
    bootstrapped = true;
    return;
  }
  for (const email of BOOTSTRAP) {
    await pool.query(
      `INSERT INTO members (email, role) VALUES ($1, 'owner') ON CONFLICT (email) DO NOTHING`,
      [email],
    );
  }
  bootstrapped = true;
}

// ---- 判定 (middleware から) ----
// 締め出しをすぐ効かせたいので長くは覚えない
const cache = new Map<string, { who: Who | null; at: number }>();
const TTL = 5_000;

async function cached(key: string, f: () => Promise<Who | null>): Promise<Who | null> {
  const hit = cache.get(key);
  if (hit && Date.now() - hit.at < TTL) return hit.who;
  const who = await f();
  cache.set(key, { who, at: Date.now() });
  return who;
}

export function forget(): void {
  cache.clear();
}

export async function memberByEmail(email: string): Promise<Member | null> {
  await ensureOwners();
  const r = await pool.query("SELECT * FROM members WHERE email = $1", [email.toLowerCase()]);
  return r.rows[0] ? toMember(r.rows[0]) : null;
}

export async function identifyEmail(email: string): Promise<Who | null> {
  return cached(`e:${email.toLowerCase()}`, async () => {
    const m = await memberByEmail(email);
    return m ? { member: m, deviceId: null, via: "web" } : null;
  });
}

export async function identifyDevice(token: string): Promise<Who | null> {
  if (!token.startsWith(DEVICE_PREFIX)) return null;
  return cached(`d:${hashToken(token)}`, async () => {
    const r = await pool.query(
      `SELECT m.*, s.device_id FROM device_sessions s JOIN members m ON m.id = s.member_id
       WHERE s.token_hash = $1 AND s.revoked_at IS NULL`,
      [hashToken(token)],
    );
    if (!r.rows[0]) return null;
    pool
      .query("UPDATE device_sessions SET last_seen = now() WHERE token_hash = $1", [hashToken(token)])
      .catch(() => {});
    return { member: toMember(r.rows[0]), deviceId: String(r.rows[0].device_id), via: "device" };
  });
}

// ---- 端末のログイン ----
/** アプリの端末に新しいログインを渡す。同じ人の同じ端末の前のログインは切る */
export async function createDeviceSession(memberId: number, deviceId: string): Promise<string> {
  // 頭にテナントを入れる (main 以外は fr1_<id>_)。Caddy がこれを見て、端末の要求をテナントの管制室へ回す
  const token = newToken(deviceTokenPrefix(TENANT_ID));
  await pool.query(
    "UPDATE device_sessions SET revoked_at = now() WHERE member_id = $1 AND device_id = $2 AND revoked_at IS NULL",
    [memberId, deviceId],
  );
  await pool.query(
    "INSERT INTO device_sessions (token_hash, member_id, device_id) VALUES ($1, $2, $3)",
    [hashToken(token), memberId, deviceId],
  );
  forget();
  return token;
}

// ---- Google の身元証明 (アプリの Google ログイン) ----
const JWKS = createRemoteJWKSet(new URL("https://www.googleapis.com/oauth2/v3/certs"));

/**
 * アプリが Google ログインで受け取った ID トークンを確かめる。宛先 (aud) は管制室の Web クライアント
 * (AUTH_GOOGLE_ID) — アプリはこの ID を serverClientId に指定してログインする。
 * @return 確かめられたメールアドレスと名前。だめなら null
 */
export async function verifyGoogleIdToken(idToken: string): Promise<{ email: string; name: string } | null> {
  const aud = process.env.AUTH_GOOGLE_ID ?? "";
  if (!aud) return null;
  try {
    const { payload } = await jwtVerify(idToken, JWKS, {
      issuer: ["https://accounts.google.com", "accounts.google.com"],
      audience: aud,
    });
    if (payload.email_verified !== true || typeof payload.email !== "string") return null;
    return { email: payload.email.toLowerCase(), name: typeof payload.name === "string" ? payload.name : "" };
  } catch {
    return null;
  }
}

// ---- 招待 ----
const INVITE_DAYS = 7;

export async function createInvite(email: string, role: Role, createdBy: number): Promise<string> {
  const token = newToken("inv_");
  await pool.query(
    `INSERT INTO invites (token_hash, email, role, created_by, expires_at)
     VALUES ($1, $2, $3, $4, now() + interval '${INVITE_DAYS} days')`,
    [hashToken(token), email.toLowerCase(), role, createdBy],
  );
  return token;
}

export type Invite = { tokenHash: string; email: string; role: Role; expiresAt: string };

/** まだ使える招待 (受けていない・取り消していない・期限内) */
export async function openInvite(token: string): Promise<Invite | null> {
  const r = await pool.query(
    `SELECT * FROM invites WHERE token_hash = $1
       AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()`,
    [hashToken(token)],
  );
  const row = r.rows[0];
  if (!row || !isRole(row.role)) return null;
  return { tokenHash: row.token_hash, email: row.email, role: row.role, expiresAt: new Date(row.expires_at).toISOString() };
}

/** そのメールアドレス宛ての、まだ使える招待があるか (Google ログインを通してよいか) */
export async function hasOpenInviteFor(email: string): Promise<boolean> {
  const r = await pool.query(
    `SELECT 1 FROM invites WHERE email = $1 AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at > now()`,
    [email.toLowerCase()],
  );
  return r.rows.length > 0;
}

/**
 * 招待を受ける。⚠招待したメールアドレスと Google アカウントが同じときだけ (なりすまし対策)。
 * 締め出されている人は招待でも戻れない (戻すのはオーナーが画面で)
 */
export async function acceptInvite(
  token: string,
  email: string,
  name: string,
): Promise<{ ok: true; member: Member } | { ok: false; error: string }> {
  const inv = await openInvite(token);
  if (!inv) return { ok: false, error: "この招待は使えません (期限切れか、取り消されたか、もう使われています)" };
  if (inv.email !== email.toLowerCase()) {
    return { ok: false, error: `この招待は ${inv.email} 宛てです。そのアカウントでログインしてください` };
  }
  const existing = await memberByEmail(email);
  if (existing?.status === "banned") return { ok: false, error: "このアカウントは利用を止められています" };
  // ⚠既にメンバーの人が招待を受けても権限は下げない (2026-10-04)。オーナーが閲覧の招待を踏むと
  //   オーナーが誰もいなくなる。権限を下げるのはメンバー欄の操作で (最後のオーナーの確認がある)
  const role = existing && atLeast(existing.role, inv.role) ? existing.role : inv.role;
  const r = await pool.query(
    `INSERT INTO members (email, name, role) VALUES ($1, $2, $3)
     ON CONFLICT (email) DO UPDATE SET role = EXCLUDED.role, name = COALESCE(NULLIF(EXCLUDED.name, ''), members.name)
     RETURNING *`,
    [email.toLowerCase(), name, role],
  );
  await pool.query("UPDATE invites SET accepted_at = now() WHERE token_hash = $1", [inv.tokenHash]);
  forget();
  return { ok: true, member: toMember(r.rows[0]) };
}

/** リクエストのヘッダ (middleware が付ける) から、誰のリクエストか */
export function memberFromHeaders(h: { get(name: string): string | null }): {
  id: number;
  role: string;
  name: string;
  email: string;
} | null {
  const id = Number(h.get("x-member-id") ?? "");
  if (!id) return null;
  return {
    id,
    role: h.get("x-member-role") ?? "",
    name: decodeURIComponent(h.get("x-member-name") ?? ""),
    email: h.get("x-member-email") ?? "",
  };
}
