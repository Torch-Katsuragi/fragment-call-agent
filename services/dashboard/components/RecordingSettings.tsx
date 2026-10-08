"use client";

import { useCallback, useEffect, useState } from "react";

// 録音の後始末 (2026-10-02)。ユーザー「古い録音の削除はユーザーがやればいい。設定で期限を決めて
// 自動削除、範囲指定での削除も」。消すのは録音だけで、文字起こしと通話記録 md は残る。
// 実際に消すのは worker (自動は 1 時間ごと、期間指定は agent_jobs 経由ですぐ)
type Summary = { count: number; bytes: number; oldest: string | null; range_count: number | null };

const KEEP_CHOICES = [0, 30, 90, 180, 365];

function mb(bytes: number) {
  return bytes < 1e6 ? `${Math.round(bytes / 1e3)}KB` : `${(bytes / 1e6).toFixed(1)}MB`;
}

export default function RecordingSettings() {
  const [days, setDays] = useState<number | null>(null);
  const [sum, setSum] = useState<Summary | null>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);

  const loadSummary = useCallback(async (f = from, t = to) => {
    const q = new URLSearchParams();
    if (f) q.set("from", f);
    if (t) q.set("to", t);
    const r = await fetch(`/api/recordings?${q}`, { cache: "no-store" });
    if (r.ok) setSum(await r.json());
  }, [from, to]);

  useEffect(() => {
    fetch("/api/settings", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((d) => setDays(Number(d.recording_retention_days ?? 0)))
      .catch(() => setDays(0));
    loadSummary("", "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (from || to) loadSummary(from, to);
  }, [from, to, loadSummary]);

  const saveDays = async (v: number) => {
    const prev = days;
    setDays(v);
    const r = await fetch("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recording_retention_days: v }),
    });
    if (!r.ok) {
      setDays(prev);
      setMsg((await r.json().catch(() => ({}))).error ?? "保存できませんでした");
    }
  };

  const deleteRange = async () => {
    const n = sum?.range_count ?? 0;
    if (!n) return;
    const span = `${from || "最初"} 〜 ${to || "最後"}`;
    if (!window.confirm(`${span} の録音 ${n} 件を消します。元に戻せません。文字起こしは残ります。`)) return;
    setBusy(true);
    setMsg("");
    try {
      const r = await fetch("/api/recordings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ from, to }),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        setMsg(d.error ?? "消せませんでした");
        return;
      }
      // worker が拾うまで待つ (毎秒巡回なので数秒)
      for (let i = 0; i < 30; i++) {
        await new Promise((ok) => setTimeout(ok, 1000));
        const s = await fetch(`/api/recordings?job=${d.job}`, { cache: "no-store" }).then((x) => x.json());
        if (s.status === "done") {
          setMsg(`${s.deleted} 件消しました`);
          break;
        }
        if (s.status === "error") {
          setMsg(`消せませんでした: ${s.error ?? ""}`);
          break;
        }
        if (i === 29) setMsg("受け付けました。反映まで少しかかります");
      }
      await loadSummary();
    } finally {
      setBusy(false);
    }
  };

  if (days === null) return null;
  const choices = KEEP_CHOICES.includes(days) ? KEEP_CHOICES : [...KEEP_CHOICES, days].sort((a, b) => a - b);

  return (
    <div className="set-group">
      <div className="set-row inline">
        <div className="set-row-text">
          <div className="set-row-title">保存している録音</div>
          <div className="set-row-desc">
            作業フォルダの 録音/ に置いています。通話の画面と通話記録から聞けます
          </div>
        </div>
        <span className="set-val-inline">
          {sum ? (sum.count ? `${sum.count} 件 ・ ${mb(sum.bytes)}` : "なし") : "…"}
        </span>
      </div>

      <div className="set-row inline">
        <div className="set-row-text">
          <div className="set-row-title">古い録音を自動で消す</div>
          <div className="set-row-desc">1 時間ごとに、この日数より古い録音を消します</div>
        </div>
        <select value={days} onChange={(e) => saveDays(Number(e.target.value))}>
          {choices.map((d) => (
            <option key={d} value={d}>
              {d === 0 ? "消さない" : `${d}日より古いもの`}
            </option>
          ))}
        </select>
      </div>

      <div className="set-row">
        <div className="set-row-title">期間を指定して消す</div>
        <div className="set-row-desc">
          消すのは録音だけです。文字起こしと通話記録は残ります{sum?.oldest ? ` ・ いちばん古い録音は ${sum.oldest}` : ""}
        </div>
        <div className="member-invite">
          <input type="date" value={from} max={to || undefined} onChange={(e) => setFrom(e.target.value)} />
          <span className="muted">〜</span>
          <input type="date" value={to} min={from || undefined} onChange={(e) => setTo(e.target.value)} />
        </div>
        <div className="week-actions" style={{ marginTop: 8 }}>
          <button
            className="btn-quiet"
            onClick={deleteRange}
            disabled={busy || !(from || to) || !sum?.range_count}
          >
            {from || to ? `この期間の ${sum?.range_count ?? 0} 件を消す` : "期間を選んでください"}
          </button>
          {msg && <span className="set-row-desc">{msg}</span>}
        </div>
      </div>
    </div>
  );
}
