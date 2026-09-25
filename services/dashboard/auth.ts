import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import { extractDeviceToken, verifyDeviceToken } from "@/lib/deviceToken";

// 管制室は本人専用ツールのため、許可アカウント以外はログインさせない。
// カンマ区切りで複数許可も可 (ALLOWED_EMAILS)。⚠未設定なら誰も入れない (安全側)
const ALLOWED_EMAILS = (process.env.ALLOWED_EMAILS ?? "")
  .split(",")
  .map((e) => e.trim().toLowerCase())
  .filter(Boolean);

// ⚠ AUTH_URL は本番(VM)では必ず設定する。未設定だと next-auth が自分のURLを
//   `https://localhost:3000` と誤認し、未ログイン時の callbackUrl がそこを指して認証後に戻れない。
//   ・Hostヘッダー由来ではない: Host/X-Forwarded-Host に公開ホストを入れても localhost:3000 のまま
//     (scheme だけは X-Forwarded-Proto を反映する)。PORT からの既定値なのでヘッダーでは直らない
//   ・2026-07-24に「固定するとローカルのバイパスが壊れる」として避けたが、7/26に
//     authorized() の判定を NODE_ENV へ移した時点でホスト固定の副作用は消えている
//   ・ローカル(next dev)では設定しない。設定すると localhost での開発が公開ホスト扱いになる
//   実測・修正: 2026-07-30 (VMの services/dashboard/.env.local に AUTH_URL を追記)
export const { handlers, signIn, signOut, auth } = NextAuth({
  // Caddy(リバースプロキシ)配下で動くため、Hostヘッダーの検証をNext.jsのプロキシ信頼に委ねる
  trustHost: true,
  providers: [Google],
  callbacks: {
    signIn({ user }) {
      return !!user.email && ALLOWED_EMAILS.includes(user.email.toLowerCase());
    },
    // middleware(auth as middleware)がルート保護するかどうかはこれで決まる。
    // signInコールバックだけではログイン許可の絞り込みにしかならず、未ログイン時のリダイレクトは発生しない
    async authorized({ auth, request }) {
      // ローカル開発 (next dev) だけ認証を省く。**本番ビルド (next start) では必ず要求する**。
      // ⚠ 以前は request.nextUrl.hostname が localhost かで判定していたが、
      //   Caddy が upstream へ Host: localhost:3000 で転送するため公開URLでも
      //   バイパスが成立し、未ログインで管制室が丸見えになっていた (2026-07-26に実測)。
      //   ホスト名はプロキシ次第で信用できない — 環境で判定し、既定は閉じる (fail-closed)
      if (process.env.NODE_ENV === "development") return true;
      if (auth?.user) return true;
      // スマホアプリ (専用電話アプリ) の端末トークン。
      // ⚠WebView の中では Google ログインができない (`disallowed_useragent`) ので、
      //   アプリだけはこの経路で入る。詳細と失効のさせ方は lib/deviceToken.ts の冒頭コメント。
      // ⚠ここは Edge runtime なので DB は引けない — 署名の検証だけで判定している
      return !!(await verifyDeviceToken(extractDeviceToken(request)));
    },
  },
  pages: {
    signIn: "/login",
  },
});
