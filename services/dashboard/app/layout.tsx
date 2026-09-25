import type { Metadata, Viewport } from "next";
import Sidebar from "@/components/Sidebar";
import AccountCard from "@/components/AccountCard";
import CallSessionProvider from "@/components/CallSession";
import GlobalRinging from "@/components/GlobalRinging";
import SecurityAlert from "@/components/SecurityAlert";
import DialerProvider from "@/components/Dialer";
import BottomNav from "@/components/BottomNav";
import "./globals.css";
// ⚠globals の後に読む。スマホ用の上書きが効く順序にしておく
import "./mobile.css";

export const metadata: Metadata = {
  title: "フラグメント 管制室",
  description: "AI電話エージェント「フラグメント」の管制室 (ライブ文字起こし + 通話履歴)",
};

// ⚠**これが無いとWebViewは「デスクトップ幅980px」でレイアウトして全体を縮小表示する**
//   (2026-08-01に専用電話アプリの実機で発覚。字が小さく右が切れていた)。
//   PCブラウザでは差が出ないので、スマホアプリを作るまで露呈しなかった。
//   ⚠ズームは許可したまま — 文字起こしを読む画面なので、拡大を封じない
export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ja">
      <body>
        <div className="shell">
          <Sidebar footer={<AccountCard />} />
          <main>
            <CallSessionProvider>
              <DialerProvider>
                <GlobalRinging />
                <SecurityAlert />
                {children}
              </DialerProvider>
            </CallSessionProvider>
          </main>
          {/* ⚠スマホ専用。PCではCSSで消える (サイドバーが担当) */}
          <BottomNav />
        </div>
      </body>
    </html>
  );
}
