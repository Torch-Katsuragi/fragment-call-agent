import NextAuth from "next-auth";
import Google from "next-auth/providers/google";
import { pool } from "@/lib/db";
import { hasOpenInviteFor, memberByEmail } from "@/lib/members";
import { TENANT_ID, tenantsOfEmail } from "@/lib/tenants";

// 管制室に Google でログインしてよいのは、メンバー表 (lib/members.ts) にいる人と、招待を受けに来た人だけ
// (2026-09-26。それまでは ALLOWED_EMAILS の固定リスト。今は ALLOWED_EMAILS は最初のオーナーの種)。
// ⚠どのページ・API をどの権限で開けるかは middleware.ts が毎回判定する。ここはログインの門だけ

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
    async signIn({ user }) {
      const email = user.email?.toLowerCase();
      if (!email) return false;
      const m = await memberByEmail(email);
      if (m) {
        if (m.status !== "active") return "/login?error=banned";
        // 名前は Google アカウントのものに揃える (ほかの端末に「〇〇が応対中」と出る名前)
        if (user.name && user.name !== m.name) {
          await pool.query("UPDATE members SET name = $2 WHERE id = $1", [m.id, user.name]).catch(() => {});
        }
        return true;
      }
      // メンバーでなくても、そのアドレス宛ての招待があれば通す (招待のページで受ける)
      if (await hasOpenInviteFor(email)) return true;
      // 同居構成 (2026-10-04): 他の管制室のメンバーなら通す。middleware がその管制室へ回す
      return (await tenantsOfEmail(email)).some((t) => t.id !== TENANT_ID);
    },
  },
  pages: {
    signIn: "/login",
  },
});
