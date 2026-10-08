import { redirect } from "next/navigation";
import { auth, signIn, signOut } from "@/auth";
import { acceptInvite, hashToken, openInvite } from "@/lib/members";
import { TENANT_ID, tenantOfInvite } from "@/lib/tenants";
import { ROLE_LABEL } from "@/lib/roles";
import { publicBase } from "@/lib/publicBase";

export const dynamic = "force-dynamic";
export const metadata = { title: "招待 | フラグメント" };

// 招待のページ (2026-09-26)。招待メールのリンクの行き先。
// ⚠Android でアプリが入っていれば、このページは開かずにアプリが開く (App Links。/.well-known/assetlinks.json)。
//   ここに来るのは、アプリが無いか、ブラウザで使う人。
//   ・アプリで開く … fragment://invite?server=…&token=… (アプリはここから Google ログインして参加する)
//   ・ブラウザで使う … Google でログイン → 招待したアドレスと同じなら、その場でメンバーになる
// ⚠ログイン無しで開ける (middleware の public)。招待の中身 (宛先・権限) だけを見せる
export default async function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  const inv = await openInvite(token);
  if (!inv) {
    // 同居構成 (2026-10-04): 他の管制室の招待なら、そちらへ回す (Cookie を付け替えて同じページを開き直す)
    const owner = await tenantOfInvite(hashToken(token));
    if (owner && owner.id !== TENANT_ID) {
      redirect(`/api/public/tenant?to=${encodeURIComponent(owner.id)}&next=${encodeURIComponent(`/invite/${token}`)}`);
    }
  }
  const session = await auth();
  const email = session?.user?.email?.toLowerCase() ?? "";
  const base = publicBase("");
  const appLink = `fragment://invite?server=${encodeURIComponent(base)}&token=${encodeURIComponent(token)}`;

  let error = "";
  if (inv && email) {
    if (email === inv.email) {
      const r = await acceptInvite(token, email, session?.user?.name ?? "");
      if (r.ok) redirect("/");
      error = r.error;
    } else {
      error = `いまは ${email} でログインしています。この招待は ${inv.email} 宛てです。`;
    }
  }

  return (
    <div className="invite">
      <h1 className="page-title">フラグメントへの招待</h1>
      {!inv ? (
        <p className="set-row-desc">この招待は使えません (期限切れか、取り消されたか、もう使われています)。</p>
      ) : (
        <>
          <p>
            <b>{inv.email}</b> 宛ての招待です。権限は「{ROLE_LABEL[inv.role]}」。
          </p>
          <p className="set-row-desc">
            期限 {new Date(inv.expiresAt).toLocaleString("ja-JP")}。このメールアドレスの Google アカウントで参加してください。
          </p>
          {error && <p style={{ color: "#f87171" }}>{error}</p>}
          <div className="invite-actions">
            <a className="btn-primary" href={appLink}>
              アプリで開く
            </a>
            {email && email !== inv.email ? (
              <form
                action={async () => {
                  "use server";
                  await signOut({ redirectTo: `/invite/${token}` });
                }}
              >
                <button className="btn-quiet" type="submit">
                  ログアウトして別のアカウントで
                </button>
              </form>
            ) : (
              <form
                action={async () => {
                  "use server";
                  await signIn("google", { redirectTo: `/invite/${token}` });
                }}
              >
                <button className="btn-quiet" type="submit">
                  ブラウザで使う (Google でログイン)
                </button>
              </form>
            )}
          </div>
        </>
      )}
    </div>
  );
}
