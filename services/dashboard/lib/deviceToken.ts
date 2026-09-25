// 端末トークン — スマホアプリが管制室に入るための鍵。
//
// ⚠**Edge runtime で動く**。middleware (auth.ts の authorized) から呼ばれるため:
//   ・`lib/env.ts` を import してはいけない (node:path と dotenv を使っていて Edge で落ちる)
//   ・Postgres は引けない (pg は Edge 非対応)
//   だから DB のトークン表ではなく **HMAC署名で自己完結** させている。
//
// ⚠なぜ必要か: WebView の中では Google ログインができない (`disallowed_useragent` で拒否される)。
//   管制室は全パスを OAuth で保護している (auth.ts、本番は fail-closed) ので、
//   アプリは OAuth ではなくこのトークンで入る。LiveKit の operator トークン取得にも同じものを使う。
//
// ⚠**このトークンは管制室の全体にアクセスできる**。WebView が管制室そのものを表示する以上、
//   スコープを /api/device/* に絞ることはできない。漏れた場合の被害は「管制室が丸見え」。
//   だからトークンは長く、失効させられるようにしてある (下記)。
//
// ⚠**失効のさせ方**: `.env` の `DEVICE_TOKEN_VERSION` を別の値にして dashboard を再起動する。
//   発行済みトークンが**全部**無効になるので、端末を再ペアリングする。
//   端末は1〜2台の想定なので、個別失効のためだけに DB を引く仕組みは持たない
//   (持てない — 上記のとおり middleware から DB が見えない)。

const enc = new TextEncoder();

/** 発行済みトークンの世代。これを変えると既存のトークンが全部無効になる */
export const DEVICE_TOKEN_VERSION = process.env.DEVICE_TOKEN_VERSION ?? "1";

export type DevicePayload = {
  /** 端末の名前 (どの端末か分かるようにするだけ。認可には使わない) */
  n: string;
  /** 世代 */
  v: string;
  /** 発行時刻 (epoch秒) */
  iat: number;
};

function b64url(bytes: Uint8Array): string {
  let s = "";
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

// ⚠戻り値に型注釈を付けない。`: Uint8Array` と書くと ArrayBufferLike 付きに広がって
//   crypto.subtle.verify の BufferSource に代入できなくなる (TS 5.7以降)。
//   ArrayBuffer から作れば Uint8Array<ArrayBuffer> に推論される
function b64urlDecode(s: string) {
  const t = s.replace(/-/g, "+").replace(/_/g, "/");
  const bin = atob(t + "=".repeat((4 - (t.length % 4)) % 4));
  const out = new Uint8Array(new ArrayBuffer(bin.length));
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

async function hmacKey(): Promise<CryptoKey> {
  // next-auth と同じ秘密を使う。⚠ AUTH_SECRET は本番では next-auth 自体が必須にしている
  const secret = process.env.AUTH_SECRET ?? process.env.NEXTAUTH_SECRET ?? "";
  if (!secret) throw new Error("AUTH_SECRET is not set");
  return crypto.subtle.importKey(
    "raw",
    enc.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

export async function signDeviceToken(name: string): Promise<string> {
  const payload: DevicePayload = {
    n: name,
    v: DEVICE_TOKEN_VERSION,
    iat: Math.floor(Date.now() / 1000),
  };
  const body = b64url(enc.encode(JSON.stringify(payload)));
  const sig = new Uint8Array(await crypto.subtle.sign("HMAC", await hmacKey(), enc.encode(body)));
  return `${body}.${b64url(sig)}`;
}

/** 検証。⚠どんな失敗も null に倒す (fail-closed) */
export async function verifyDeviceToken(token: string | null | undefined): Promise<DevicePayload | null> {
  if (!token) return null;
  const dot = token.indexOf(".");
  if (dot <= 0) return null;
  const body = token.slice(0, dot);
  const sig = token.slice(dot + 1);
  try {
    const ok = await crypto.subtle.verify(
      "HMAC",
      await hmacKey(),
      b64urlDecode(sig),
      enc.encode(body),
    );
    if (!ok) return null;
    const p = JSON.parse(new TextDecoder().decode(b64urlDecode(body))) as DevicePayload;
    // 世代が違う = 失効済み
    if (p.v !== DEVICE_TOKEN_VERSION) return null;
    return p;
  } catch {
    return null;
  }
}

/** リクエストからトークンを取り出す。Authorizationヘッダ (アプリのAPI呼び出し) と Cookie (WebView) の両方 */
export function extractDeviceToken(req: {
  headers: { get(name: string): string | null };
  cookies: { get(name: string): { value: string } | undefined };
}): string | null {
  const auth = req.headers.get("authorization");
  if (auth?.startsWith("Bearer ")) return auth.slice(7).trim();
  return req.cookies.get("device_token")?.value ?? null;
}
