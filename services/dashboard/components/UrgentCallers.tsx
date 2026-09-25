"use client";

import { useEffect, useState } from "react";

// 緊急呼び出しを許可する相手の管理UI。
// ⚠候補は機械が挙げるが、**許可を与えるのは本人のクリックだけ**。
//   発信者番号は偽装され得るので、番号や名乗りを根拠に自動許可はしない
//   (2026-08-01の調査。詳細は lib/urgentCallers.ts の冒頭)

type Row = { number: string; name: string | null; last_at: string; reason: string; calls: number };

export default function UrgentCallers() {
  const [allowed, setAllowed] = useState<Row[]>([]);
  const [candidates, setCandidates] = useState<Row[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  // 候補は畳んでおく (2026-09-24)。並べると「許可」ボタンが縦に続いて重い
  const [showCand, setShowCand] = useState(false);

  const load = async () => {
    try {
      const res = await fetch("/api/urgent", { cache: "no-store" });
      if (!res.ok) return;
      const d = await res.json();
      setAllowed(d.allowed ?? []);
      setCandidates(d.candidates ?? []);
    } finally {
      setLoaded(true);
    }
  };

  useEffect(() => {
    load();
  }, []);

  const decide = async (number: string, allow: boolean) => {
    setBusy(number);
    try {
      await fetch("/api/urgent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ number, allow }),
      });
      await load();
    } finally {
      setBusy(null);
    }
  };

  const label = (r: Row) => r.name ?? r.number;

  return (
    <>
      <div className="set-group">
        {!loaded && (
          <div className="set-row">
            <span className="muted">読み込み中…</span>
          </div>
        )}
        {loaded && allowed.length === 0 && candidates.length === 0 && (
          <div className="set-row">
            <div className="set-row-desc">
              候補はまだありません。自分が話した・発信した相手がここに挙がります。
            </div>
          </div>
        )}
        {allowed.map((r) => (
          <div key={r.number} className="set-row inline">
            <div className="set-row-text">
              <div className="set-row-title">{label(r)}</div>
              <div className="set-row-desc">{r.name ? r.number : r.reason}</div>
            </div>
            <button className="btn-quiet" onClick={() => decide(r.number, false)} disabled={busy === r.number}>
              解除
            </button>
          </div>
        ))}
        {candidates.length > 0 && (
          <div className="set-row inline">
            <div className="set-row-text">
              <div className="set-row-title">候補 {candidates.length} 件</div>
              <div className="set-row-desc">自分が話した・発信した相手</div>
            </div>
            <button className="btn-quiet" onClick={() => setShowCand((v) => !v)}>
              {showCand ? "閉じる" : "表示"}
            </button>
          </div>
        )}
        {showCand && candidates.map((r) => (
          <div key={r.number} className="set-row inline">
            <div className="set-row-text">
              <div className="set-row-title">
                {label(r)}
                <span className="badge off">候補</span>
              </div>
              <div className="set-row-desc">
                {r.name ? `${r.number} ・ ` : ""}
                {r.calls}件
              </div>
            </div>
            <div style={{ display: "flex", gap: 6 }}>
              <button onClick={() => decide(r.number, true)} disabled={busy === r.number}>
                許可
              </button>
              <button className="btn-quiet" onClick={() => decide(r.number, false)} disabled={busy === r.number}>
                不要
              </button>
            </div>
          </div>
        ))}
      </div>
      <p className="set-foot">
        許可した相手だけが、急ぎのときにスマホを鳴らせます。⚠番号は偽装できるため、自動では許可しません。
      </p>
    </>
  );
}
