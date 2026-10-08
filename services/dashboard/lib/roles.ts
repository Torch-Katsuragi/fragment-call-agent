// 権限の段階と、パスごとに要る権限 (2026-09-26)。middleware.ts が毎リクエストこれで判定する。
// Edge でも Node でも読めるように、ここには DB も node: モジュールも入れない。
//
//   owner     … 全部 (メンバーの管理を含む)
//   admin     … 応答モード・電話帳・プロンプト・取り次ぎを許す相手などの設定。招待 (owner/admin 以外)
//   responder … 鳴る・視聴・会話・呼ぶ・耳打ち・発信
//   viewer    … 履歴と電話帳を見るだけ (視聴はできる。会話には入れない)

export type Role = "owner" | "admin" | "responder" | "viewer";
export const ROLES: Role[] = ["owner", "admin", "responder", "viewer"];
export const ROLE_LABEL: Record<Role, string> = {
  owner: "オーナー",
  admin: "管理",
  responder: "応対",
  viewer: "閲覧",
};
const RANK: Record<Role, number> = { viewer: 1, responder: 2, admin: 3, owner: 4 };

export function isRole(r: unknown): r is Role {
  return typeof r === "string" && r in RANK;
}

export function atLeast(role: string | null | undefined, need: Role): boolean {
  return isRole(role) && RANK[role] >= RANK[need];
}

/** ログイン無しで開ける場所 */
const PUBLIC = [
  /^\/login/,
  /^\/api\/auth\//,
  /^\/invite\//,
  /^\/api\/public\//,
  /^\/\.well-known\//,
  /^\/api\/device\/(join|login)$/,
];

/**
 * そのリクエストに要る権限。"public" = ログイン不要。
 * ⚠迷ったら重い方に倒す。細かい判定 (会話用のトークンか等) は各ルートでもう一度見る
 */
export function requiredRole(method: string, path: string): Role | "public" {
  if (PUBLIC.some((re) => re.test(path))) return "public";
  const write = method !== "GET" && method !== "HEAD";

  // 受付時間は本人も変えられる (ルートで「本人か管理以上」を見る。2026-09-29)
  if (/^\/api\/members\/(\d+|me)\/hours$/.test(path)) return "viewer";
  // メンバーと招待。招待できる権限の細かい線引きはルート側 (admin は owner/admin を招待できない)
  if (path.startsWith("/api/members")) return write ? "admin" : "viewer";

  // 回線全体に効く設定
  if (/^\/api\/(settings|prompts|security|urgent|schedule)/.test(path) && write) return "admin";
  if (path === "/api/device/mode" && write) return "admin";
  if (path.startsWith("/api/phonebook") && write) return "admin";
  // 録音の削除 (2026-10-02)。取り返しがつかないので回線の設定と同じ重さ
  if (path.startsWith("/api/recordings") && write) return "admin";

  // 通話に手を出す操作
  if (write && /^\/api\/(calls\/|dial|pickup)/.test(path)) return "responder";
  if (write && /^\/api\/device\/(handoff|pickup|decide)/.test(path)) return "responder";

  // 端末自身の登録 (プッシュ・一時停止) と、見るだけのもの
  return "viewer";
}
