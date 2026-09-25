"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

/**
 * スマホ用の下部タブバー。
 *
 * ⚠**これが無いとスマホでは設定にも警備にも行けない** (2026-08-01)。
 *   サイドバーを `display:none` で隠しただけだったので、ナビゲーションごと消えていた。
 *   隠すのではなく、指の届く位置に置き直すのが「スマホ用に設計する」ということ。
 *
 * ⚠デスクトップでは出さない (CSSで `display:none`)。PC はサイドバーが担当する。
 * ⚠`env(safe-area-inset-bottom)` を効かせるので、ジェスチャーバーに潜り込まない。
 */
const TABS = [
  { href: "/", label: "ホーム", icon: "🏠" },
  { href: "/history", label: "履歴", icon: "🕘" },
  { href: "/security", label: "警備", icon: "🛡" },
  { href: "/settings", label: "設定", icon: "⚙" },
] as const;

export default function BottomNav() {
  const pathname = usePathname();
  // スマホのロック画面用の軽量表示 (/live/<id>) には操作を置かない (LiveView.tsx)
  if (pathname.startsWith("/live/")) return null;
  return (
    <nav className="bottom-nav" aria-label="メインメニュー">
      {TABS.map((t) => {
        // ⚠「/」は前方一致だと全ページで光るので完全一致で見る
        const on = t.href === "/" ? pathname === "/" : pathname.startsWith(t.href);
        return (
          <Link
            key={t.href}
            href={t.href}
            className={`bottom-nav-item ${on ? "on" : ""}`}
            aria-current={on ? "page" : undefined}
          >
            <span className="bottom-nav-icon" aria-hidden>
              {t.icon}
            </span>
            <span className="bottom-nav-label">{t.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
