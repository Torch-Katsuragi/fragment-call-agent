import Link from "next/link";
import { auth } from "@/auth";

// サイドバー左下のアカウントカード。
// サブタイトルは「メールアドレス」— 電話番号は出さない。
// 1アカウントで複数回線を持つ設計なので、番号はここではなく設定の「回線」に一覧で置く
export default async function AccountCard() {
  const session = await auth();
  const user = session?.user;

  // user が居ないのは ①ログイン画面 ②next dev の認証バイパス (auth.ts の authorized) の2通り。
  // ⚠ 以前は "認証なし (localhost)" と決め打ちしていたが、公開URLのログイン画面でも
  //    そう表示されて「localhostとして動いている」ように見え誤診を誘った (2026-07-30に修正)。
  //    ホストは判定できない/すべきでないので、文言はどちらでも正しい中立な表現にする
  const name = user?.name ?? "未ログイン";
  const sub = user?.email ?? "認証が必要です";
  const initial = (user?.name ?? "?").trim().charAt(0);

  return (
    <Link href="/settings" className="account-card" title="設定を開く">
      {user?.image ? (
        // next/image を使うと外部ドメイン許可設定が要るので素の img
        // eslint-disable-next-line @next/next/no-img-element
        <img className="account-avatar" src={user.image} alt="" referrerPolicy="no-referrer" />
      ) : (
        <span className="account-avatar account-avatar-fallback">{initial}</span>
      )}
      <span className="account-text">
        <span className="account-name">{name}</span>
        <span className="account-sub">{sub}</span>
      </span>
      <span className="account-gear" aria-hidden>
        ⚙
      </span>
    </Link>
  );
}
