import path from "node:path";
import { config } from "dotenv";

// 秘密情報はリポジトリ直下の .env に一元化 (dashboard 専用の .env.local は作らない)
config({ path: path.resolve(process.cwd(), "..", "..", ".env") });

export const LIVEKIT_API_KEY = process.env.LIVEKIT_API_KEY ?? "devkey";
export const LIVEKIT_API_SECRET = process.env.LIVEKIT_API_SECRET ?? "";
// サーバ側 (Room API の hangup 等) が叩く URL。同一ホストなので平文でよい
export const LIVEKIT_WS_URL = process.env.LIVEKIT_URL ?? "ws://localhost:7880";
export const LIVEKIT_HTTP_URL = LIVEKIT_WS_URL.replace(/^ws/, "http");

// ⚠ ブラウザに渡す URL は別で持つ (2026-07-30に半日溶かした)。
// 管制室をHTTPSで公開すると、平文の ws:// には繋げない —
//   SecurityError: An insecure WebSocket connection may not be initiated from a page loaded over HTTPS
// ブラウザはルームに入れず、通話画面を開いても「参加者は agent と sip だけ」になり
// 双方向とも無音になる (文字起こしはDB経由なので出てしまい、原因が見えにくい)。
// 公開環境では Caddy 越しの wss:// を LIVEKIT_PUBLIC_URL に入れること。
// サーバ側と同じ値にしてはいけない — 自分の公開IP宛はGCPでヘアピンせず Room API が死ぬ
export const LIVEKIT_PUBLIC_WS_URL = process.env.LIVEKIT_PUBLIC_URL ?? LIVEKIT_WS_URL;

// 通話履歴DB (compose の postgres。127.0.0.1:5432 に公開されている)
export const DATABASE_URL =
  process.env.DATABASE_URL ?? "postgresql://callagent:callagent@localhost:5432/callagent";

// フラグメント専用ワークスペース (Google Drive内)。電話帳mdのname解決に使う
export const FRAGMENT_WORKSPACE =
  process.env.FRAGMENT_WORKSPACE ?? "./workspace";

// ===== 回線 (設定画面の「回線」セクション用) =====
// 1アカウントで複数番号を持てる設計。番号はアカウントの属性ではなく回線の属性として扱う
// (アカウントカードに番号を出すと複数番号で破綻するため、こちらに一覧で持たせる — 2026-07-26)
export type LineInfo = {
  id: string;
  label: string;
  number: string;
  kind: string;
  role: string;
  configured: boolean;
};

export const LINES: LineInfo[] = [
  {
    id: "brastel",
    label: "ブラステル My050",
    number: process.env.LINE_BRASTEL_NUMBER ?? "",
    kind: "REGISTER型",
    role: "公開番号 (受電・発信)",
    configured: !!process.env.BRASTEL_SIP_USER,
  },
  {
    id: "twilio",
    label: "Twilio Elastic SIP Trunking",
    number: process.env.LINE_TWILIO_NUMBER ?? "",
    kind: "peer型 (非REGISTER)",
    role: "検証用 (法人向け経路の確認)",
    configured: !!process.env.TWILIO_SIP_USER,
  },
];

// ===== 音声パイプラインの構成 (設定画面に読み取り専用で表示) =====
// 変更にはコンテナ再起動が要るため、UIからは編集させない (誤操作で通話が壊れるのを避ける)
const TTS_PROVIDER = process.env.TTS_PROVIDER ?? "gemini";

export const PIPELINE = {
  aiCore: process.env.AI_CORE ?? "live",
  sttProvider: process.env.STT_PROVIDER ?? "deepgram",
  ttsProvider: TTS_PROVIDER,
  // 既定値は docker-compose.yml の ${...:-default} と揃える (ズレると診断表示が嘘になる)
  voice:
    TTS_PROVIDER === "google"
      ? (process.env.GOOGLE_TTS_VOICE ?? "ja-JP-Chirp3-HD-Leda")
      : (process.env.GEMINI_VOICE ?? "Leda"),
};

// ===== FCM (スマホアプリを起こすプッシュ、2026-09-18) =====
// アプリに配る Firebase の接続情報。google-services.json をアプリに焼かないのは、
// self-host ごとに Firebase プロジェクトが違うため (アプリは1つのビルドで全サーバーに繋げる)。
// 4つ揃わなければ null = アプリはプッシュ無し (ロングポーリングのみ) で動く
export type FcmClientConfig = {
  projectId: string;
  senderId: string;
  appId: string;
  apiKey: string;
};

export function fcmClientConfig(): FcmClientConfig | null {
  const c = {
    projectId: process.env.FCM_PROJECT_ID ?? "",
    senderId: process.env.FCM_SENDER_ID ?? "",
    appId: process.env.FCM_ANDROID_APP_ID ?? "",
    apiKey: process.env.FCM_ANDROID_API_KEY ?? "",
  };
  return c.projectId && c.senderId && c.appId && c.apiKey ? c : null;
}
