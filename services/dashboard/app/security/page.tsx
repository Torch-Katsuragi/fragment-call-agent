"use client";

import { useCallback, useEffect, useState } from "react";

// 警備画面 (2026-07-26): 検知イベントの一覧・確認と、ブロックリストの手動操作。
// 検知・遮断そのものは hookd (/guard) と agent (最終防衛線) が services/agent/security.py で行う。
// ここは「人間が気づいて判断する」ための窓口 — 2026-07-26に欠けていたのはまさにこれ

type Event = {
  id: number;
  first_at: string;
  last_at: string;
  count: number;
  kind: string;
  severity: string;
  source: string;
  action: string;
  caller_number: string | null;
  callee_number: string | null;
  detail: string;
  acknowledged: boolean;
};

type Blocked = {
  number: string;
  reason: string;
  blocked_until: string | null;
  hits: number;
  created_at: string;
};

// ブロック候補 = 実際に着信のあった番号 (通話履歴・検知イベント・着信試行から集約)。
// 手入力は打ち間違いで「ブロックしたつもりが効いていない」を作るので、原則こちらから選ぶ
type Candidate = {
  number: string;
  last_at: string | null;
  calls: number;
  kind: string | null;
};

const KIND_LABEL: Record<string, string> = {
  premium_relay: "国際プレミアム番号への中継",
  international_relay: "国際番号への中継",
  unknown_callee: "自分の番号宛でない着信",
  extension_scan: "内線番号の総当たり",
  malformed_caller: "不正な発信者番号",
  rate_limit: "短時間の連続着信",
  blocked_repeat: "ブロック中の番号からの再着信",
  storm: "着信ストーム",
};

const ACTION_LABEL: Record<string, string> = {
  reject: "自動拒否",
  observe: "検知のみ (様子見モード)",
  alert: "警報",
};

const fmt = (s: string) =>
  new Date(s).toLocaleString("ja-JP", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });

export default function SecurityPage() {
  const [events, setEvents] = useState<Event[]>([]);
  const [blocked, setBlocked] = useState<Blocked[]>([]);
  const [candidates, setCandidates] = useState<Candidate[]>([]);
  const [last24h, setLast24h] = useState({ rejected: 0, total: 0 });
  const [unacked, setUnacked] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const [manual, setManual] = useState("");

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/security", { cache: "no-store" });
      if (!res.ok) throw new Error(`読み込めません (${res.status})`);
      const data = await res.json();
      setEvents(data.events ?? []);
      setBlocked(data.blocked ?? []);
      setCandidates(data.candidates ?? []);
      setLast24h(data.last24h ?? { rejected: 0, total: 0 });
      setUnacked(data.unacked ?? 0);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 10000);
    return () => clearInterval(id);
  }, [load]);

  const post = async (body: unknown) => {
    await fetch("/api/security", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    await load();
  };

  // 番号は原則「候補から選ぶ」。手入力はブラウザのprompt()ではなく画面内の入力欄で受ける
  // (prompt()は見た目が浮くうえに、打ち間違いをそのまま登録してしまう)
  const block = async (number: string, reason: string) => {
    if (!number.trim()) return;
    await post({ block: number, reason });
    setPicking(false);
    setManual("");
  };

  // 既にブロック中の番号にブロックボタンを出さないための集合
  const blockedNumbers = new Set(blocked.map((b) => b.number));

  return (
    <>
      <div className="toolbar" style={{ justifyContent: "space-between" }}>
        <h1 style={{ margin: 0 }}>🛡 警備</h1>
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <span className="muted">
            直近24時間: 着信 {last24h.total} 件中 {last24h.rejected} 件を拒否
          </span>
          <button onClick={() => setPicking((v) => !v)}>
            {picking ? "閉じる" : "＋ 番号をブロック"}
          </button>
          {unacked > 0 && <button onClick={() => post({ ack: "all" })}>すべて確認済みにする</button>}
        </div>
      </div>

      {error && <div className="panel">⚠ {error}</div>}

      {picking && (
        <div className="panel">
          <div style={{ marginBottom: 8 }}>
            <b>ブロックする番号を選ぶ</b>
            <span className="muted"> — 着信のあった番号から選べます（恒久ブロック）</span>
          </div>
          {candidates.length === 0 && (
            <div className="muted" style={{ marginBottom: 10 }}>
              着信履歴がまだありません。下の欄に直接入力してください。
            </div>
          )}
          <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 12 }}>
            {candidates.map((c) => (
              <div key={c.number} className="sec-blocked" style={{ padding: "6px 0" }}>
                <span className="sec-kind">{c.number}</span>
                <span className="muted">
                  {c.calls > 0 && `通話 ${c.calls} 件`}
                  {c.kind && `${c.calls > 0 ? " / " : ""}検知: ${KIND_LABEL[c.kind] ?? c.kind}`}
                  {c.last_at && ` / 最終 ${fmt(c.last_at)}`}
                </span>
                <span className="bar-spacer" />
                <button
                  onClick={() =>
                    block(
                      c.number,
                      c.kind
                        ? `管制室から手動でブロック (検知: ${KIND_LABEL[c.kind] ?? c.kind})`
                        : "管制室から手動でブロック (着信履歴から選択)",
                    )
                  }
                >
                  ⛔ ブロック
                </button>
              </div>
            ))}
          </div>
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
            <span className="muted">一覧に無い番号:</span>
            <input
              value={manual}
              onChange={(e) => setManual(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") block(manual, "管制室から手動でブロック (手入力)");
              }}
              placeholder="09012345678"
              inputMode="tel"
              style={{ flex: "0 0 200px" }}
            />
            <button
              onClick={() => block(manual, "管制室から手動でブロック (手入力)")}
              disabled={!manual.trim()}
            >
              ブロック
            </button>
          </div>
        </div>
      )}

      <h2 className="section-title">検知イベント</h2>
      {events.length === 0 && (
        <div className="panel muted">
          検知はありません。不審な着信 (内線番号の総当たり・国際番号への中継・同一発信者の
          連続着信) を検知すると、AIが応答する前に自動拒否してここに記録します。
        </div>
      )}
      {events.map((e) => (
        <div
          key={e.id}
          className={`panel sec-event ${e.severity} ${e.acknowledged ? "acked" : ""}`}
        >
          <div className="sec-head">
            <span className="sec-kind">
              {e.severity === "critical" ? "🚨" : "⚠"} {KIND_LABEL[e.kind] ?? e.kind}
            </span>
            <span className={`sec-action ${e.action}`}>{ACTION_LABEL[e.action] ?? e.action}</span>
            {e.count > 1 && <span className="sec-count">×{e.count}</span>}
            <span className="bar-spacer" />
            <span className="muted">
              {fmt(e.first_at)}
              {e.count > 1 && ` 〜 ${fmt(e.last_at)}`} / {e.source}
            </span>
            {/* 検知を見てその場で判断できるのが警備画面の役目なので、行から直接ブロックさせる */}
            {e.caller_number && !blockedNumbers.has(e.caller_number) && (
              <button
                onClick={() =>
                  block(
                    e.caller_number!,
                    `管制室から手動でブロック (検知: ${KIND_LABEL[e.kind] ?? e.kind})`,
                  )
                }
                title={`${e.caller_number} を恒久ブロック`}
              >
                ⛔
              </button>
            )}
            {!e.acknowledged && (
              <button onClick={() => post({ ack: e.id })} title="確認済みにする">
                ✓
              </button>
            )}
          </div>
          <div className="sec-nums">
            発信者: <b>{e.caller_number ?? "非通知/不明"}</b>
            {e.callee_number && <> → 宛先: <b>{e.callee_number}</b></>}
          </div>
          <div className="muted">{e.detail}</div>
        </div>
      ))}

      <h2 className="section-title">ブロック中の番号</h2>
      {blocked.length === 0 && <div className="panel muted">ブロック中の番号はありません。</div>}
      {blocked.map((b) => (
        <div key={b.number} className="panel sec-blocked">
          <span className="sec-kind">{b.number}</span>
          <span className="muted">
            {b.blocked_until ? `${fmt(b.blocked_until)} まで` : "恒久ブロック"}
            {b.hits > 1 && ` / 再着信 ${b.hits} 回`}
          </span>
          <span className="bar-spacer" />
          <span className="muted sec-reason" title={b.reason}>
            {b.reason}
          </span>
          <button onClick={() => post({ unblock: b.number })}>解除</button>
        </div>
      ))}
    </>
  );
}
