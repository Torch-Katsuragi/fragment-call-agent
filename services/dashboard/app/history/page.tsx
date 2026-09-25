"use client";

import { useEffect, useState } from "react";
import Link from "next/link";

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

// 通話履歴の全画面。
//
// ⚠**スマホ用に足したページ** (2026-08-01)。PCでは履歴はサイドバーが担当するが、
//   スマホではサイドバーが出ないので、履歴を見る場所が無かった。
//   ⚠サイドバーを隠すだけで済ませていたのが元の誤り — 隠した機能の行き先を作る。
// PCから開いても普通に使える (サイドバーより1件あたりの情報が多い)。
export default function HistoryPage() {
  const [calls, setCalls] = useState<CallItem[] | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const res = await fetch("/api/calls?limit=200", { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        if (alive) setCalls(data);
      } catch {
        if (alive) setCalls([]);
      }
    };
    poll();
    const id = setInterval(poll, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  if (calls === null) return <div className="muted">読み込み中…</div>;
  if (calls.length === 0) return <div className="cloud-empty">通話履歴はまだありません</div>;

  const active = calls.filter((c) => c.active);
  const past = calls.filter((c) => !c.active);

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

  return (
    <>
      <h1 className="page-title">通話履歴</h1>
      {active.length > 0 && (
        <>
          <div className="section-title">通話中</div>
          <div className="hist-list">{active.map((c) => <HistRow key={c.id} c={c} />)}</div>
        </>
      )}
      {[...groups.entries()].map(([day, items]) => (
        <div key={day}>
          <div className="section-title">{day}</div>
          <div className="hist-list">{items.map((c) => <HistRow key={c.id} c={c} />)}</div>
        </div>
      ))}
    </>
  );
}

function HistRow({ c }: { c: CallItem }) {
  const time = new Date(c.started_at).toLocaleTimeString("ja-JP", {
    hour: "2-digit",
    minute: "2-digit",
  });
  const label = c.caller_name ?? c.caller_number ?? "非通知/不明";
  // 一覧から中身が分かるように、直近の1往復を出す。⚠タップ領域を稼ぐ意味もある
  const preview = (c.preview ?? []).slice(-2);
  return (
    <Link href={`/call/${c.id}`} className="hist-row">
      <div className="hist-head">
        {c.active && <span className="pulse-dot" />}
        <span className="hist-name">
          {c.direction === "outbound" && <span className="muted">↗ </span>}
          {label}
        </span>
        <span className="hist-time">{time}</span>
      </div>
      {preview.length > 0 && (
        <div className="hist-preview">
          {preview.map((p, i) => (
            <div key={i}>
              <span className={`speaker ${p.speaker}`}>
                {p.speaker === "caller" ? "相手" : p.speaker === "ai" ? "AI" : "本人"}
              </span>{" "}
              {p.text}
            </div>
          ))}
        </div>
      )}
    </Link>
  );
}
