// 送信だけのメール (2026-09-26、招待)。⚠返信は受けない (ユーザー「外部にも普及させるなら下手に返信を
// 受け付けない方がいい」)。送信元は MAIL_FROM (例: フラグメント <noreply@example.com>)。
// 手段は Resend の HTTP API (RESEND_API_KEY)。self-host では各自の送信サービスの鍵に差し替える。
// 鍵が無ければ送らずに false を返す — 招待リンクは管制室に出るので、手で渡せる
const KEY = process.env.RESEND_API_KEY ?? "";
const FROM = process.env.MAIL_FROM ?? "";

export function mailEnabled(): boolean {
  return !!(KEY && FROM);
}

export async function sendMail(to: string, subject: string, text: string): Promise<boolean> {
  if (!mailEnabled()) return false;
  try {
    const res = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { Authorization: `Bearer ${KEY}`, "Content-Type": "application/json" },
      body: JSON.stringify({ from: FROM, to: [to], subject, text }),
    });
    return res.ok;
  } catch {
    return false;
  }
}
