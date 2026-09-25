"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

type CallItem = {
  id: string;
  caller_number: string | null;
  caller_name: string | null;
  started_at: string;
  ended_at: string | null;
  direction: string;
  active: boolean;
  preview: { speaker: string; text: string }[];
};

// footer にはサーバーコンポーネント (AccountCard) を layout から差し込む。
// Sidebar 自体は通話履歴のポーリングがあるのでクライアントのまま
export default function Sidebar({ footer }: { footer?: React.ReactNode }) {
  const [calls, setCalls] = useState<CallItem[]>([]);
  const pathname = usePathname();
  // スマホのロック画面用の軽量表示 (/live/<id>) には操作を置かない (LiveView.tsx)
  if (pathname.startsWith("/live/")) return null;

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const res = await fetch("/api/calls?limit=100", { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        if (alive) setCalls(data);
      } catch {
        /* DB未起動時はサイドバーを空のままにする */
      }
    };
    poll();
    const id = setInterval(poll, 2000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const active = calls.filter((c) => c.active);
  const past = calls.filter((c) => !c.active);

  // 履歴を日付でグループ化
  const groups = new Map<string, CallItem[]>();
  for (const c of past) {
    const day = new Date(c.started_at).toLocaleDateString("ja-JP", {
      month: "long",
      day: "numeric",
      weekday: "short",
    });
    const arr = groups.get(day) ?? [];
    arr.push(c);
    groups.set(day, arr);
  }

  const item = (c: CallItem) => {
    const href = `/call/${c.id}`;
    const time = new Date(c.started_at).toLocaleTimeString("ja-JP", {
      hour: "2-digit",
      minute: "2-digit",
    });
    // 表示は電話帳のname優先。番号はツールチップに (詳細画面にも出る)
    const label = c.caller_name ?? c.caller_number ?? "非通知/不明";
    return (
      <Link key={c.id} href={href}>
        <div
          className={`side-item ${pathname === href ? "current" : ""}`}
          title={c.caller_number ?? "非通知/不明"}
        >
          {c.active && <span className="pulse-dot" />}
          <span className="side-label">
            {c.direction === "outbound" && <span className="muted">↗ </span>}
            {label}
          </span>
          <span className="side-time">{time}</span>
        </div>
      </Link>
    );
  };

  return (
    <nav className="sidebar">
      <Link href="/">
        <div className="side-title">🧩 フラグメント</div>
      </Link>
      <Link href="/security">
        <div className={`side-item ${pathname === "/security" ? "current" : ""}`}>
          <span className="side-label">🛡 警備</span>
        </div>
      </Link>
      {/* 履歴だけをスクロールさせ、アカウントカードは常に左下に残す */}
      <div className="side-scroll">
        {active.length > 0 && (
          <>
            <div className="side-group">通話中</div>
            {active.map(item)}
          </>
        )}
        {[...groups.entries()].map(([day, items]) => (
          <div key={day}>
            <div className="side-group">{day}</div>
            {items.map(item)}
          </div>
        ))}
        {calls.length === 0 && <div className="side-group">通話履歴はまだありません</div>}
      </div>
      {footer}
    </nav>
  );
}
