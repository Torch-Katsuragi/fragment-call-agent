"use client";

import { useEffect, useState } from "react";
import WeekEditor, { type Days } from "./WeekEditor";
import { DAY_KEYS, DAY_LABEL } from "@/lib/schedule";

// メンバーと招待 (2026-09-26)。
// ・招待: メールアドレスと権限を入れて「招待」。送信の設定があればメールで届く (返信は受けない)。
//   設定が無いときはリンクが出るので、手で渡す
// ・権限の変更と締め出しはオーナーだけ。締め出すとその人の端末も全部切れる

type Role = "owner" | "admin" | "responder" | "viewer";
const ROLE_LABEL: Record<Role, string> = { owner: "オーナー", admin: "管理", responder: "応対", viewer: "閲覧" };
const ROLES: Role[] = ["owner", "admin", "responder", "viewer"];

type Member = {
  id: number;
  email: string;
  name: string;
  role: Role;
  status: string;
  devices: number;
  /** 担当 (2026-10-04)。保留中に呼ぶ相手を AI が並べ替える材料。端末ではなく人に持たせる */
  duty: string;
  /** 受付時間 (2026-09-29)。null = いつでも */
  hours: { days: Days } | null;
};
/** 受付時間を一行で。「いつでも」「月〜金 8:00〜17:00」など */
function hoursText(h: Member["hours"]): string {
  if (!h || !Object.keys(h.days ?? {}).length) return "いつでも";
  const groups: { days: string[]; text: string }[] = [];
  for (const k of DAY_KEYS) {
    const b = h.days[k];
    if (!b?.length) continue;
    const text = b.map((x) => `${x.start}〜${x.end}`).join(", ");
    const last = groups.at(-1);
    if (last && last.text === text) last.days.push(DAY_LABEL[k]);
    else groups.push({ days: [DAY_LABEL[k]], text });
  }
  return groups
    .map((g) => `${g.days.length > 2 ? `${g.days[0]}〜${g.days.at(-1)}` : g.days.join("・")} ${g.text}`)
    .join(" / ");
}
type Invite = { token_hash: string; email: string; role: Role; expires_at: string; mailed_at: string | null };

export default function MembersSettings() {
  const [members, setMembers] = useState<Member[]>([]);
  const [invites, setInvites] = useState<Invite[]>([]);
  const [me, setMe] = useState<{ id: number; role: string } | null>(null);
  const [mail, setMail] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("responder");
  const [result, setResult] = useState<{ link: string; mailed: boolean } | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // 受付時間を編集している人と、編集中の値
  const [hoursFor, setHoursFor] = useState<number | null>(null);
  const [hoursDraft, setHoursDraft] = useState<Days>({});

  const load = async () => {
    try {
      const res = await fetch("/api/members", { cache: "no-store" });
      if (!res.ok) return;
      const d = await res.json();
      setMembers(d.members ?? []);
      setInvites(d.invites ?? []);
      setMe(d.me ?? null);
      setMail(!!d.mail);
    } finally {
      setLoaded(true);
    }
  };
  useEffect(() => {
    load();
  }, []);

  const owner = me?.role === "owner";
  const canInvite = owner || me?.role === "admin";

  const invite = async () => {
    setError("");
    setResult(null);
    setBusy(true);
    try {
      const res = await fetch("/api/members", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, role }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        setError(d.error ?? "招待できませんでした");
        return;
      }
      setResult({ link: d.link, mailed: !!d.mailed });
      setEmail("");
      await load();
    } finally {
      setBusy(false);
    }
  };

  const patch = async (id: number, body: object, confirmText?: string) => {
    if (confirmText && !window.confirm(confirmText)) return;
    const res = await fetch(`/api/members/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      window.alert(d.error ?? "変更できませんでした");
    }
    await load();
  };

  const saveHours = async (id: number, days: Days | null) => {
    const res = await fetch(`/api/members/${id}/hours`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hours: days && Object.keys(days).length ? { days } : null }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      window.alert(d.error ?? "保存できませんでした");
      return;
    }
    setHoursFor(null);
    await load();
  };

  const revoke = async (hash: string) => {
    await fetch(`/api/members/invites/${hash}`, { method: "DELETE" });
    await load();
  };

  return (
    <>
      <div className="set-group">
        {!loaded && (
          <div className="set-row">
            <span className="muted">読み込み中…</span>
          </div>
        )}
        {members.map((m) => (
          <div key={m.id}>
          <div className="set-row inline">
            <div className="set-row-text">
              <div className="set-row-title">
                {m.name || m.email}
                {m.status === "banned" && <span className="badge off"> 締め出し中</span>}
              </div>
              <div className="set-row-desc">
                {m.name ? `${m.email} · ` : ""}端末 {m.devices} 台
              </div>
              <div className="set-row-desc">
                担当 {m.duty || "なし"}
                {owner && (
                  <button
                    className="btn-quiet"
                    onClick={() => {
                      const v = window.prompt(
                        `${m.name || m.email} の担当 (例: 林産・経理)。保留中に呼ぶ相手を選ぶときの手がかりになります`,
                        m.duty ?? "",
                      );
                      if (v !== null) patch(m.id, { duty: v });
                    }}
                  >
                    変える
                  </button>
                )}
              </div>
              <div className="set-row-desc">
                受付 {hoursText(m.hours)}
                {(m.id === me?.id || owner || me?.role === "admin") && hoursFor !== m.id && (
                  <button
                    className="btn-quiet"
                    onClick={() => {
                      setHoursFor(m.id);
                      setHoursDraft(m.hours?.days ?? {});
                    }}
                  >
                    変える
                  </button>
                )}
              </div>
            </div>
            {owner && m.id !== me?.id ? (
              <div className="member-ops">
                <select value={m.role} onChange={(e) => patch(m.id, { role: e.target.value })}>
                  {ROLES.map((r) => (
                    <option key={r} value={r}>
                      {ROLE_LABEL[r]}
                    </option>
                  ))}
                </select>
                {m.status === "banned" ? (
                  <button className="btn-quiet" onClick={() => patch(m.id, { status: "active" })}>
                    戻す
                  </button>
                ) : (
                  <button
                    className="btn-quiet danger"
                    onClick={() =>
                      patch(
                        m.id,
                        { status: "banned" },
                        `${m.name || m.email} を締め出しますか？その人の端末もすべて切れます。`,
                      )
                    }
                  >
                    締め出す
                  </button>
                )}
              </div>
            ) : (
              <span className="set-val-inline">{ROLE_LABEL[m.role]}</span>
            )}
          </div>
          {hoursFor === m.id && (
            <div className="set-row">
              <div className="set-row-desc">
                この時間だけ {m.name || m.email} の端末を鳴らします。時間帯が 1 つも無ければいつでも鳴ります。
              </div>
              <WeekEditor days={hoursDraft} onChange={setHoursDraft} withMode={false} />
              <div className="week-actions">
                <button className="btn-quiet" onClick={() => saveHours(m.id, hoursDraft)}>
                  保存
                </button>
                <button className="btn-quiet" onClick={() => saveHours(m.id, null)}>
                  いつでも鳴らす
                </button>
                <button className="btn-quiet" onClick={() => setHoursFor(null)}>
                  やめる
                </button>
              </div>
            </div>
          )}
          </div>
        ))}
        {invites.map((i) => (
          <div key={i.token_hash} className="set-row inline">
            <div className="set-row-text">
              <div className="set-row-title">{i.email}</div>
              <div className="set-row-desc">
                招待中 · {ROLE_LABEL[i.role]} · {new Date(i.expires_at).toLocaleDateString("ja-JP")} まで
                {i.mailed_at ? " · メール送信済み" : ""}
              </div>
            </div>
            {canInvite && (
              <button className="btn-quiet" onClick={() => revoke(i.token_hash)}>
                取り消す
              </button>
            )}
          </div>
        ))}
      </div>

      {canInvite && (
        <div className="set-group" style={{ marginTop: 12 }}>
          <div className="set-row">
            <div className="set-row-title">招待する</div>
            <div className="member-invite">
              <input
                type="email"
                placeholder="メールアドレス (Google アカウント)"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
              <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
                {ROLES.filter((r) => owner || (r !== "owner" && r !== "admin")).map((r) => (
                  <option key={r} value={r}>
                    {ROLE_LABEL[r]}
                  </option>
                ))}
              </select>
              <button className="btn-quiet" onClick={invite} disabled={busy || !email}>
                招待
              </button>
            </div>
            {error && <div className="set-row-desc" style={{ color: "#f87171" }}>{error}</div>}
            {result && (
              <div className="set-row-desc">
                {result.mailed ? "メールで送りました。" : "メールの送信は設定されていません。このリンクを渡してください:"}
                <div className="member-link">
                  <code>{result.link}</code>
                  <button className="btn-quiet" onClick={() => navigator.clipboard?.writeText(result.link)}>
                    コピー
                  </button>
                </div>
              </div>
            )}
            {!mail && !result && (
              <div className="set-row-desc">メールの送信は未設定です (招待のリンクを手で渡します)。</div>
            )}
          </div>
        </div>
      )}
      <p className="set-foot">オーナー: 全部 · 管理: 設定と招待 · 応対: 着信・会話・呼ぶ · 閲覧: 見るだけ</p>
    </>
  );
}
