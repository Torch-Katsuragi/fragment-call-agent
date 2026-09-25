import { redirect } from "next/navigation";
import { auth, signIn } from "@/auth";

// ⚠callbackUrl は同一サイト内の絶対パスに限る。外部URLを許すとログイン直後に
//   任意サイトへ飛ばせる (open redirect) ので、先頭が "/" で "//" でないものだけ通す
function safeCallback(raw: string | undefined): string {
  if (!raw) return "/";
  return raw.startsWith("/") && !raw.startsWith("//") ? raw : "/";
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ error?: string; callbackUrl?: string }>;
}) {
  const { error, callbackUrl } = await searchParams;
  const next = safeCallback(callbackUrl);

  // ⚠ログイン済みでここに来たら戻す (2026-09-18)。
  //   以前はこの分岐が無く、OAuth から /login に戻ってきたまま止まっていた —
  //   サイドバーにはアカウントカードと履歴が出ているのに、中央は「Googleでログイン」のまま
  //   という画面になり、本人に「ログインしたのにボタンが残る。バグ？」と言われた。
  //   signIn("google") にも redirectTo が無かったので、認証後の戻り先が /login 自身だった
  const session = await auth();
  if (session?.user) redirect(next);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: "12px",
        height: "100vh",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      {error && <p style={{ color: "#f87171" }}>ログインエラー: {error}</p>}
      <form
        action={async () => {
          "use server";
          await signIn("google", { redirectTo: next });
        }}
      >
        <button
          type="submit"
          style={{
            padding: "12px 24px",
            fontSize: "16px",
            borderRadius: "8px",
            border: "1px solid #444",
            cursor: "pointer",
          }}
        >
          Googleでログイン
        </button>
      </form>
    </div>
  );
}
