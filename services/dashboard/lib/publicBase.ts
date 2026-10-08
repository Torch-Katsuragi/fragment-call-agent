// 外から見える管制室の URL (招待リンク・アプリの接続先)。本番は AUTH_URL (auth.ts の注記)。
// ⚠リクエストの Host は Caddy 越しだと localhost:3000 になるので当てにしない
export function publicBase(fallback: string): string {
  return (process.env.AUTH_URL ?? fallback).replace(/\/+$/, "");
}
