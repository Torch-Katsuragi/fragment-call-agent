"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

// 警備アラートのグローバル帯 (2026-07-26)。
// 2026-07-26のトールフラウド侵入で本当に足りなかったのは遮断ではなく「気づく手段」だったので、
// 未確認の検知が1件でもあれば、どの画面にいても目に入る位置に出す。
// 詳細と確認 (ack) は /security。通知 (Discord/Slack) は hookd 側が別途投げる
export default function SecurityAlert() {
  const [unacked, setUnacked] = useState(0);
  const [critical, setCritical] = useState(false);
  const pathname = usePathname();

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const res = await fetch("/api/security?limit=1", { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        if (!alive) return;
        setUnacked(data.unacked ?? 0);
        setCritical(Boolean(data.critical));
      } catch {
        /* DB/hookd未起動時は何も出さない */
      }
    };
    poll();
    const id = setInterval(poll, 10000); // 警備は秒単位の即時性より常時見えていることが大事
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [pathname]);

  if (unacked === 0 || pathname === "/security") return null;

  return (
    <Link href="/security">
      <div className={`security-banner ${critical ? "critical" : ""}`}>
        <span>{critical ? "🚨" : "⚠"}</span>
        <span className="security-banner-label">
          不審な着信を{unacked}件検知しました
        </span>
        <span className="muted">詳細を見る →</span>
      </div>
    </Link>
  );
}
