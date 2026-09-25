export { auth as middleware } from "@/auth";

export const config = {
  // /api/auth/* (ログインフロー自体) と静的アセットは除外、それ以外は全ページ・全API認証必須
  matcher: ["/((?!api/auth|_next/static|_next/image|favicon.ico).*)"],
};
