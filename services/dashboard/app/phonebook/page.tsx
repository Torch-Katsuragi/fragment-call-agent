"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import Toggle from "@/components/Toggle";
import { useDialer } from "@/components/Dialer";
import type { Role } from "@/lib/roles";

type ContactAnswer = "人が出る" | "AIが出る" | null;
type ContactUrgent = "allowed" | "denied" | "unset";

type ContactItem = {
  number: string;
  name: string | null;
  kana: string | null;
  memo: string;
  recent: string;
  lookup: string;
  people: string;
  unsorted: string;
  conflicts: string;
  answer: ContactAnswer;
  urgent: ContactUrgent;
  call_count: number;
  last_call_at: string | null;
  last_call_id: string | null;
};

type FilterKey = "all" | "human" | "ai" | "urgent" | "conflict";

function formatWhen(iso: string | null): string | null {
  if (!iso) return null;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return null;
  return d.toLocaleString("ja-JP", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function conflictLinesOf(c: ContactItem): string[] {
  return [
    ...c.conflicts.split("\n"),
    ...c.lookup.split("\n").filter((l) => l.trim().startsWith("- ⚠")),
  ]
    .map((l) => l.trim().replace(/^- /, ""))
    .filter(Boolean);
}

function cleanLines(sec: string, skipWarn = false): string[] {
  return sec
    .split("\n")
    .map((l) => l.trim())
    .filter((l) => l.length > 0 && (!skipWarn || !l.startsWith("- ⚠")));
}

export default function PhonebookPage() {
  const { dial, dialing } = useDialer();
  const [contacts, setContacts] = useState<ContactItem[] | null>(null);
  const [role, setRole] = useState<Role>("viewer");
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<FilterKey>("all");
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

  // 新規追加フォーム
  const [addOpen, setAddOpen] = useState(false);
  const [addNumber, setAddNumber] = useState("");
  const [addName, setAddName] = useState("");
  const [addKana, setAddKana] = useState("");
  const [addMemo, setAddMemo] = useState("");
  const [addBusy, setAddBusy] = useState(false);
  const [addErr, setAddErr] = useState<string | null>(null);

  // 行内の編集フォーム
  const [editNum, setEditNum] = useState<string | null>(null);
  const [editName, setEditName] = useState("");
  const [editKana, setEditKana] = useState("");
  const [editMemo, setEditMemo] = useState("");
  const [busyNum, setBusyNum] = useState<string | null>(null);
  const [notice, setNotice] = useState<Record<string, string>>({});

  const canEdit = role === "owner" || role === "admin";
  const canDial = role !== "viewer";
  const dialBusy = dialing?.status === "dialing" || dialing?.status === "answered";

  const load = useCallback(async () => {
    try {
      const res = await fetch("/api/phonebook", { cache: "no-store" });
      if (!res.ok) throw new Error(`電話帳を取得できません (${res.status})`);
      const d = await res.json();
      setContacts(d.contacts ?? []);
      if (d.role) setRole(d.role);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const toggleExpand = (num: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(num)) next.delete(num);
      else next.add(num);
      return next;
    });
  };

  const startEdit = (c: ContactItem) => {
    setEditNum(c.number);
    setEditName(c.name ?? "");
    setEditKana(c.kana ?? "");
    setEditMemo(c.memo);
    setNotice((p) => ({ ...p, [c.number]: "" }));
  };

  const submitAdd = async () => {
    setAddErr(null);
    const num = addNumber.trim();
    if (!num) {
      setAddErr("電話番号を入力してください");
      return;
    }
    if (!addName.trim() && !addMemo.trim()) {
      setAddErr("名前またはメモを入力してください");
      return;
    }
    setAddBusy(true);
    try {
      const res = await fetch("/api/phonebook/edit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          number: num,
          name: addName,
          kana: addKana,
          memo: addMemo,
        }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        setAddErr(d.error ?? "登録できませんでした");
        return;
      }
      const savedNum: string = d.number ?? num;
      setAddNumber("");
      setAddName("");
      setAddKana("");
      setAddMemo("");
      setAddOpen(false);
      await load();
      setExpanded((prev) => new Set(prev).add(savedNum));
    } finally {
      setAddBusy(false);
    }
  };

  const submitEdit = async (number: string) => {
    setBusyNum(number);
    try {
      const res = await fetch("/api/phonebook/edit", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          number,
          name: editName,
          kana: editKana,
          memo: editMemo,
        }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        window.alert(d.error ?? "保存できませんでした");
        return;
      }
      setEditNum(null);
      setNotice((p) => ({ ...p, [number]: "保存しました（次の通話から反映されます）" }));
      await load();
    } finally {
      setBusyNum(null);
    }
  };

  const changeAnswer = async (number: string, answer: ContactAnswer) => {
    setBusyNum(number);
    try {
      const res = await fetch("/api/phonebook/answer", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ number, answer }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        window.alert(d.error ?? "変更できませんでした");
        return;
      }
      await load();
    } finally {
      setBusyNum(null);
    }
  };

  const changeUrgent = async (number: string, allow: boolean) => {
    setBusyNum(number);
    try {
      const res = await fetch("/api/urgent", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ number, allow }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        window.alert(d.error ?? "変更できませんでした");
        return;
      }
      await load();
    } finally {
      setBusyNum(null);
    }
  };

  const handleDial = async (number: string, name: string | null) => {
    const label = name ? `${name} (${number})` : number;
    if (!window.confirm(`${label} に発信します (通話料がかかります)。よろしいですか？`)) {
      return;
    }
    const err = await dial(number);
    if (err) window.alert(`発信できませんでした: ${err}`);
  };

  const all = contacts ?? [];
  const conflictTotal = all.filter((c) => conflictLinesOf(c).length > 0).length;
  const q = query.trim().toLowerCase();
  const qDigits = q.replace(/\D/g, "");

  const filtered = all.filter((c) => {
    if (filter === "human" && c.answer !== "人が出る") return false;
    if (filter === "ai" && c.answer !== "AIが出る") return false;
    if (filter === "urgent" && c.urgent !== "allowed") return false;
    if (filter === "conflict" && conflictLinesOf(c).length === 0) return false;
    if (!q) return true;
    const hitText =
      (c.name?.toLowerCase().includes(q) ?? false) ||
      (c.kana?.toLowerCase().includes(q) ?? false) ||
      c.number.toLowerCase().includes(q) ||
      c.memo.toLowerCase().includes(q) ||
      c.people.toLowerCase().includes(q) ||
      c.lookup.toLowerCase().includes(q);
    if (hitText) return true;
    return qDigits.length >= 2 && c.number.includes(qDigits);
  });

  return (
    <div className="phonebook-page">
      <div className="toolbar">
        <h1 className="page-title" style={{ margin: 0 }}>
          電話帳
        </h1>
        {contacts !== null && <span className="muted">{all.length} 件</span>}
        <div style={{ marginLeft: "auto" }}>
          {canEdit && (
            <button onClick={() => setAddOpen((v) => !v)}>
              {addOpen ? "閉じる" : "+ 連絡先を追加"}
            </button>
          )}
        </div>
      </div>

      {addOpen && canEdit && (
        <div className="panel pb-add-panel">
          <div className="pb-form-grid">
            <label className="pb-field">
              <span>電話番号</span>
              <input
                type="tel"
                placeholder="09012345678"
                value={addNumber}
                onChange={(e) => setAddNumber(e.target.value)}
              />
            </label>
            <label className="pb-field">
              <span>名前 (相手・会社名)</span>
              <input
                type="text"
                placeholder="山田建設株式会社"
                value={addName}
                onChange={(e) => setAddName(e.target.value)}
              />
            </label>
            <label className="pb-field">
              <span>読みがな (空欄なら自動補完)</span>
              <input
                type="text"
                placeholder="やまだけんせつ"
                value={addKana}
                onChange={(e) => setAddKana(e.target.value)}
              />
            </label>
          </div>
          <label className="pb-field" style={{ marginTop: 10 }}>
            <span>メモ (関係・話し方の注意・【本人限定】情報など)</span>
            <textarea
              className="persona-editor"
              rows={4}
              placeholder="例: 取引先の担当者。見積もりの相談は用件と希望納期を聞いて伝言にする。"
              value={addMemo}
              onChange={(e) => setAddMemo(e.target.value)}
            />
          </label>
          {addErr && <div className="pb-err">⚠ {addErr}</div>}
          <div className="pb-actions" style={{ justifyContent: "flex-end", marginTop: 8 }}>
            <button className="btn-quiet" onClick={() => setAddOpen(false)} disabled={addBusy}>
              キャンセル
            </button>
            <button onClick={submitAdd} disabled={addBusy}>
              登録する
            </button>
          </div>
        </div>
      )}

      <div className="pb-search-bar">
        <input
          type="search"
          className="pb-search-input"
          placeholder="名前・読みがな・番号・メモで探す"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <div className="segmented pb-filters">
          {(
            [
              ["all", "すべて"],
              ["human", "人が出る"],
              ["ai", "AIが出る"],
              ["urgent", "緊急許可"],
              ...(conflictTotal > 0 ? ([["conflict", `食い違い (${conflictTotal})`]] as const) : []),
            ] as const
          ).map(([k, label]) => (
            <button
              key={k}
              type="button"
              className={`segmented-btn ${filter === k ? "on" : ""}`}
              onClick={() => setFilter(k)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {error && <div className="panel">⚠ {error}</div>}
      {contacts === null && !error && <div className="panel muted">読み込み中…</div>}

      {contacts !== null && filtered.length === 0 && (
        <div className="panel muted">
          {q || filter !== "all"
            ? "条件に一致する連絡先がありません。"
            : "まだ登録がありません。「+ 連絡先を追加」またはスマホアプリの電話帳タブから登録できます。"}
        </div>
      )}

      {filtered.length > 0 && (
        <div className="set-group">
          {filtered.map((c) => {
            const isOpen = expanded.has(c.number);
            const isEditing = editNum === c.number;
            const conflicts = conflictLinesOf(c);
            const lookupLines = cleanLines(c.lookup, true);
            const recentLines = cleanLines(c.recent);
            const peopleLines = cleanLines(c.people);
            const unsortedLines = cleanLines(c.unsorted);
            const initial = (c.name ?? "#").trim().charAt(0) || "#";
            const metaParts = [
              c.name ? c.number : null,
              c.kana && c.name ? c.kana : null,
              c.call_count > 0 ? `通話 ${c.call_count} 回` : null,
              formatWhen(c.last_call_at),
            ].filter(Boolean);

            return (
              <div key={c.number} className="pb-item">
                <div
                  className="set-row pb-row"
                  onClick={() => toggleExpand(c.number)}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      toggleExpand(c.number);
                    }
                  }}
                >
                  <div className="account-row" style={{ minWidth: 0, flex: 1 }}>
                    <div className="account-avatar account-avatar-fallback">{initial}</div>
                    <div className="set-row-text">
                      <div className="set-row-title" style={{ flexWrap: "wrap" }}>
                        <span className="pb-name">{c.name ?? c.number}</span>
                        {c.answer === "人が出る" && <span className="badge ok">人が出る</span>}
                        {c.answer === "AIが出る" && <span className="badge">AIが出る</span>}
                        {c.urgent === "allowed" && <span className="badge ok">緊急許可</span>}
                        {conflicts.length > 0 && (
                          <span className="badge pb-conflict-badge">⚠ 食い違い</span>
                        )}
                      </div>
                      {metaParts.length > 0 && (
                        <div className="set-row-desc">{metaParts.join(" ・ ")}</div>
                      )}
                      {!isOpen && c.memo && (
                        <div className="pb-memo-preview">{c.memo.replace(/\*\*/g, "")}</div>
                      )}
                    </div>
                  </div>
                  <div className="pb-row-right" onClick={(e) => e.stopPropagation()}>
                    {canDial && (
                      <button
                        type="button"
                        className="btn-quiet"
                        disabled={dialBusy}
                        title="この番号に発信する"
                        onClick={() => handleDial(c.number, c.name)}
                      >
                        📞 かける
                      </button>
                    )}
                    <span
                      className="pb-chevron"
                      onClick={() => toggleExpand(c.number)}
                      aria-hidden
                    >
                      {isOpen ? "▴" : "▾"}
                    </span>
                  </div>
                </div>

                {isOpen && (
                  <div className="pb-detail">
                    {isEditing ? (
                      <div className="pb-edit-box">
                        <div className="pb-form-grid">
                          <label className="pb-field">
                            <span>名前</span>
                            <input
                              type="text"
                              value={editName}
                              placeholder="相手・会社名"
                              onChange={(e) => setEditName(e.target.value)}
                            />
                          </label>
                          <label className="pb-field">
                            <span>読みがな (空欄なら自動補完)</span>
                            <input
                              type="text"
                              value={editKana}
                              placeholder="ひらがな"
                              onChange={(e) => setEditKana(e.target.value)}
                            />
                          </label>
                        </div>
                        <label className="pb-field" style={{ marginTop: 8 }}>
                          <span>相手ごとのメモ (着信時に AI と吹き出しが参照)</span>
                          <textarea
                            className="persona-editor"
                            rows={6}
                            value={editMemo}
                            placeholder="関係・話し方の注意・【本人限定】情報など"
                            onChange={(e) => setEditMemo(e.target.value)}
                          />
                        </label>
                        <div className="pb-actions" style={{ justifyContent: "flex-end" }}>
                          <button
                            type="button"
                            className="btn-quiet"
                            onClick={() => setEditNum(null)}
                            disabled={busyNum === c.number}
                          >
                            キャンセル
                          </button>
                          <button
                            type="button"
                            onClick={() => submitEdit(c.number)}
                            disabled={busyNum === c.number}
                          >
                            保存
                          </button>
                        </div>
                      </div>
                    ) : (
                      <div className="pb-memo-box">
                        <div className="pb-memo-head">
                          <span className="pb-sec-label">メモ</span>
                          {canEdit && (
                            <button
                              type="button"
                              className="btn-quiet pb-mini-btn"
                              onClick={() => startEdit(c)}
                            >
                              編集
                            </button>
                          )}
                        </div>
                        <div className={c.memo ? "pb-memo-body" : "pb-memo-empty"}>
                          {c.memo
                            ? c.memo.replace(/\*\*/g, "")
                            : "メモはまだありません（関係や応対の注意点を書くと、着信時に AI と吹き出しが参照します）"}
                        </div>
                        {notice[c.number] && (
                          <div className="set-row-desc" style={{ marginTop: 6 }}>
                            ✅ {notice[c.number]}
                          </div>
                        )}
                      </div>
                    )}

                    {conflicts.length > 0 && (
                      <div className="pb-alert-box">
                        {conflicts.map((line, idx) => (
                          <div key={idx}>{line}</div>
                        ))}
                      </div>
                    )}

                    {peopleLines.length > 0 && (
                      <div className="pb-sec">
                        <div className="pb-sec-label">話した人</div>
                        {peopleLines.map((line, idx) =>
                          line.startsWith("### ") ? (
                            <div key={idx} className="pb-person-head">
                              {line.slice(4)}
                            </div>
                          ) : (
                            <div key={idx} className="pb-sec-line">
                              {line.replace(/^- /, "")}
                            </div>
                          ),
                        )}
                      </div>
                    )}

                    {unsortedLines.length > 0 && (
                      <div className="pb-sec">
                        <div className="pb-sec-label" style={{ color: "var(--caller)" }}>
                          未整理 (名乗りの振り分け待ち)
                        </div>
                        {unsortedLines.map((line, idx) => (
                          <div key={idx} className="pb-sec-line">
                            {line.replace(/^- /, "")}
                          </div>
                        ))}
                      </div>
                    )}

                    {recentLines.length > 0 && (
                      <div className="pb-sec">
                        <div className="pb-sec-label">最近の用件</div>
                        {recentLines.map((line, idx) => (
                          <div key={idx} className="pb-sec-line">
                            {line.replace(/^- /, "")}
                          </div>
                        ))}
                      </div>
                    )}

                    {lookupLines.length > 0 && (
                      <div className="pb-sec">
                        <div className="pb-sec-label">番号検索</div>
                        {lookupLines.map((line, idx) => (
                          <div key={idx} className="pb-sec-line">
                            {line.replace(/^- /, "")}
                          </div>
                        ))}
                      </div>
                    )}

                    <div className="pb-controls">
                      <div className="pb-ctl-row">
                        <div className="set-row-text">
                          <div className="set-row-title">この相手からの電話</div>
                          <div className="set-row-desc">
                            {c.answer === "人が出る"
                              ? "不在のときも端末を鳴らし、出なければ AI が出ます"
                              : c.answer === "AIが出る"
                                ? "いつもすぐ AI が出ます (端末は鳴りません)"
                                : "回線の応答モードに従います"}
                          </div>
                        </div>
                        <div className="segmented">
                          {(
                            [
                              [null, "設定どおり"],
                              ["人が出る", "人が出る"],
                              ["AIが出る", "AIが出る"],
                            ] as const
                          ).map(([val, label]) => (
                            <button
                              key={label}
                              type="button"
                              disabled={!canEdit || busyNum === c.number}
                              className={`segmented-btn ${c.answer === val ? "on" : ""}`}
                              onClick={() => {
                                if (c.answer !== val) changeAnswer(c.number, val);
                              }}
                            >
                              {label}
                            </button>
                          ))}
                        </div>
                      </div>

                      <div className="pb-ctl-row">
                        <div className="set-row-text">
                          <div className="set-row-title">緊急呼び出しの許可</div>
                          <div className="set-row-desc">
                            急ぎの用件のときにスマホを鳴らす許可 (⚠番号は偽装され得るため信頼できる相手のみ)
                          </div>
                        </div>
                        <Toggle
                          on={c.urgent === "allowed"}
                          disabled={!canEdit || busyNum === c.number}
                          label={`${c.name ?? c.number} の緊急呼び出し許可`}
                          onChange={(next) => changeUrgent(c.number, next)}
                        />
                      </div>
                    </div>

                    <div className="pb-actions">
                      {canDial && (
                        <button
                          type="button"
                          disabled={dialBusy}
                          onClick={() => handleDial(c.number, c.name)}
                        >
                          📞 かける ({c.number})
                        </button>
                      )}
                      {c.last_call_id && (
                        <Link href={`/call/${c.last_call_id}`} className="btn-quiet">
                          最後の通話を見る →
                        </Link>
                      )}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      <p className="set-foot">
        変更はワークスペースの <code>連絡先/&lt;番号&gt;/&lt;番号&gt;.md</code> にそのまま保存され、次の通話から効きます。
      </p>
    </div>
  );
}
