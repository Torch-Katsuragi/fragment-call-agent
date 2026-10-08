// アプリの端末 (2026-09-26、2026-10-04 に整理)。
//
// ⚠端末は名前も役割も持たない (2026-10-04 ユーザー「端末ごとに見分けたいなら端末ごとに別のアカウントを入れればいい。
//   端末の名前は保持する必要がない」)。表示に出るのはログインしたメンバーの名前、担当はメンバーの duty。
//   端末の情報はログイン (device_sessions) の行だけ。見かけた時刻・起こす宛先・一時停止もそこに持つ。
// ⚠id はログイン (device_sessions) のものを正にする。middleware が x-member-device に入れる (外から付けた同名の
//   ヘッダは消してから)。アプリの自己申告 X-Device-Id だけを信じていた頃は、ログインした端末なら他の端末に
//   なりすませた
export type DeviceInfo = { id: string; name: string; memberId: number | null };

/** アプリからのリクエストなら端末の情報 (name はメンバーの名前)。管制室 (ブラウザ) からなら null */
export function deviceFrom(req: { headers: { get(name: string): string | null } }): DeviceInfo | null {
  const id = req.headers.get("x-member-device") || (req.headers.get("x-device-id") ?? "");
  if (!/^[A-Za-z0-9-]{8,64}$/.test(id)) return null;
  let name = "";
  try {
    name = decodeURIComponent(req.headers.get("x-member-name") ?? "").slice(0, 40);
  } catch {
    /* 壊れたヘッダ */
  }
  return { id, name, memberId: Number(req.headers.get("x-member-id") ?? "") || null };
}
