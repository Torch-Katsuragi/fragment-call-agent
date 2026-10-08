"use client";

import { useEffect, useState } from "react";

// 名乗りと、名前を出してよい人 (2026-09-30)。
// ・名乗り: AI の挨拶が「はい、〇〇です。AIが代わりに出ます。」になる。個人なら苗字、組織なら組織名。
//   空なら名乗らない。⚠「〇〇の携帯」にしない (固定電話にも使う)。挨拶は割り込み無効なので短く
// ・苗字と役職: AI が話の流れで「担当の山田課長にお伝えします」のように出してよい人。
//   相手が探りを入れていると AI が判断したら出さない。在否はどの場合も言わない
type Staff = { surname: string; title: string };

export default function SelfLabelSetting() {
  const [label, setLabel] = useState<string | null>(null);
  const [staff, setStaff] = useState<Staff[]>([]);
  const [saved, setSaved] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const snapshot = (l: string, s: Staff[]) => JSON.stringify([l.trim(), s]);

  useEffect(() => {
    fetch("/api/settings", { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("settings"))))
      .then((d) => {
        setLabel(d.self_label ?? "");
        setStaff(d.staff ?? []);
        setSaved(snapshot(d.self_label ?? "", d.staff ?? []));
      })
      .catch(() => setLabel(""));
  }, []);

  const save = async () => {
    setBusy(true);
    setError("");
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ self_label: label ?? "", staff: staff.filter((s) => s.surname.trim()) }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(d.error ?? "保存できませんでした");
        return;
      }
      setLabel(d.self_label ?? "");
      setStaff(d.staff ?? []);
      setSaved(snapshot(d.self_label ?? "", d.staff ?? []));
    } finally {
      setBusy(false);
    }
  };

  if (label === null) return null;
  const l = label.trim();
  const edit = (i: number, patch: Partial<Staff>) =>
    setStaff(staff.map((s, j) => (j === i ? { ...s, ...patch } : s)));
  const dirty = snapshot(label, staff.filter((s) => s.surname.trim())) !== saved;

  return (
    <div className="set-row">
      <div className="set-row-text">
        <div className="set-row-title">名乗り</div>
        <div className="set-row-desc">
          挨拶: 「{l ? `はい、${l}です。` : "はい、"}AIが代わりに出ます。」 · 個人なら苗字、組織なら組織名
        </div>
      </div>
      <div className="member-invite">
        <input
          type="text"
          placeholder="例: 山田 / 山田建設 (空なら名乗らない)"
          value={label}
          maxLength={20}
          onChange={(e) => setLabel(e.target.value)}
        />
      </div>

      <div className="set-row-title" style={{ marginTop: 14 }}>名前を出してよい人</div>
      <div className="set-row-desc">
        AI が話の流れで「担当の山田課長にお伝えします」のように出します。探りを入れてくる相手には出さず、
        今いるかどうかはどの場合も言いません。苗字だけを入れてください。
      </div>
      {staff.map((s, i) => (
        <div className="member-invite" key={i}>
          <input
            type="text"
            placeholder="苗字"
            value={s.surname}
            maxLength={10}
            style={{ flex: "0 1 140px", minWidth: 100 }}
            onChange={(e) => edit(i, { surname: e.target.value })}
          />
          <input
            type="text"
            placeholder="役職 (任意)"
            value={s.title}
            maxLength={20}
            onChange={(e) => edit(i, { title: e.target.value })}
          />
          <button className="btn-quiet" aria-label="消す" onClick={() => setStaff(staff.filter((_, j) => j !== i))}>
            ×
          </button>
        </div>
      ))}
      <div className="week-actions" style={{ marginTop: 8 }}>
        <button className="btn-quiet" onClick={() => setStaff([...staff, { surname: "", title: "" }])}>
          ＋ 人を足す
        </button>
        <button className="btn-quiet" onClick={save} disabled={busy || !dirty}>
          保存
        </button>
        {error && <span className="set-row-desc" style={{ color: "#f87171" }}>{error}</span>}
      </div>
    </div>
  );
}
