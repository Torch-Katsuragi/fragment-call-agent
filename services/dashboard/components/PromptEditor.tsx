"use client";

import { useEffect, useState } from "react";

// 応対プロンプトのセクション編集 (2026-07-30)。
// 実体はワークスペース(Google Drive同期)内のmdで、agentは**通話ごとに読み直す** →
// 保存すれば次の通話から効く。コンテナの再ビルドも再起動も要らない。
type Section = {
  key: string;
  title: string;
  desc: string;
  text: string;
  default: string;
  source: "workspace" | "default";
  path: string;
};

export default function PromptEditor() {
  const [sections, setSections] = useState<Section[] | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [open, setOpen] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [msg, setMsg] = useState<Record<string, string>>({});
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    fetch("/api/prompts", { cache: "no-store" })
      .then((r) => r.json())
      .then((d) => {
        if (d.error) return setErr(d.error);
        setSections(d.sections);
        setDrafts(Object.fromEntries(d.sections.map((s: Section) => [s.key, s.text])));
      })
      .catch(() => setErr("読み込めませんでした"));
  }, []);

  const call = async (key: string, method: "PUT" | "DELETE") => {
    setBusy(key);
    setErr(null);
    try {
      const res = await fetch("/api/prompts", {
        method,
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(method === "PUT" ? { key, text: drafts[key] } : { key }),
      });
      const d = await res.json();
      if (!res.ok) return setErr(d.error ?? "失敗しました");
      setSections((prev) => (prev ?? []).map((s) => (s.key === key ? d : s)));
      setDrafts((prev) => ({ ...prev, [key]: d.text }));
      setMsg((prev) => ({
        ...prev,
        [key]: method === "PUT" ? "保存しました。次の通話から効きます" : "既定に戻しました",
      }));
    } finally {
      setBusy(null);
    }
  };

  if (err && !sections) return <div className="set-note">⚠ {err}</div>;
  if (!sections) return <span className="muted">読み込み中…</span>;

  return (
    <>
      <div className="set-group">
      {sections.map((s) => {
        const dirty = drafts[s.key] !== s.text;
        return (
          <div key={s.key}>
            <div className="set-row inline">
              <div className="set-row-text">
                <div className="set-row-title">
                  {s.title}
                  <span className={`badge ${s.source === "workspace" ? "ok" : "off"}`}>
                    {s.source === "workspace" ? "編集済み" : "既定"}
                  </span>
                  {dirty && <span className="badge off">未保存</span>}
                </div>
                <div className="set-row-desc">{s.desc}</div>
              </div>
              <button className="btn-quiet" onClick={() => setOpen(open === s.key ? null : s.key)}>
                {open === s.key ? "閉じる" : "編集"}
              </button>
            </div>
            {open === s.key && (
              <div className="set-sub">
                <textarea
                  className="persona-editor"
                  value={drafts[s.key] ?? ""}
                  onChange={(e) => setDrafts((p) => ({ ...p, [s.key]: e.target.value }))}
                  spellCheck={false}
                  rows={18}
                />
                <div className="toolbar" style={{ justifyContent: "flex-end", gap: 8 }}>
                  <span className="muted sec-reason" title={s.path}>
                    {s.path}
                  </span>
                  <button onClick={() => call(s.key, "DELETE")} disabled={busy === s.key}>
                    既定に戻す
                  </button>
                  <button
                    className="dial-go"
                    onClick={() => call(s.key, "PUT")}
                    disabled={busy === s.key || !dirty}
                  >
                    保存
                  </button>
                </div>
              </div>
            )}
            {msg[s.key] && <div className="set-sub set-row-desc">✅ {msg[s.key]}</div>}
          </div>
        );
      })}
      </div>
      {err && <p className="set-foot">⚠ {err}</p>}
      <p className="set-foot">保存すると次の通話から効きます。</p>
    </>
  );
}
