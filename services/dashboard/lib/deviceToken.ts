// アプリの端末ごとのログイン (fr1_…) をリクエストから取り出す。中身の判定は lib/members.ts。
// ⚠2026-09-26 に旧式のペアリング (管制室が署名した合言葉。持っていれば誰でも全部の権限で入れ、
//   1 台だけ締め出すことができなかった) をやめた。今は Google アカウントでログインした端末だけ
export function extractDeviceToken(req: {
  headers: { get(name: string): string | null };
  cookies: { get(name: string): { value: string } | undefined };
}): string | null {
  const auth = req.headers.get("authorization");
  if (auth?.startsWith("Bearer ")) return auth.slice(7).trim();
  // ⚠Cookie (device_token) からは読まない (2026-10-04)。付けていたのは WebView 時代のアプリで、今のアプリは Bearer だけ
  return null;
}
