"use client";

import { useEffect, useState } from "react";
import AnswerModeControl from "@/components/AnswerModeControl";
import Link from "next/link";
import { useDialer } from "@/components/Dialer";

type CallItem = {
  id: string;
  caller_number: string | null;
  caller_name: string | null;
  room_name: string;
  started_at: string;
  ended_at: string | null;
  direction: string;
  active: boolean;
  preview: { speaker: string; text: string; seq: number }[];
};

const speakerLabel = (s: string) =>
  s === "caller" ? "相手" : s === "ai" ? "AI" : s === "whisper" ? "耳打ち" : "本人";

function CallCard({ c }: { c: CallItem }) {
  const time = new Date(c.started_at).toLocaleString("ja-JP", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
  return (
    <Link href={`/call/${c.id}`}>
      <div className={`card ${c.active ? "live" : ""}`} title={c.caller_number ?? "非通知/不明"}>
        <div className="card-head">
          <span className="card-caller">
            {c.direction === "outbound" ? "↗" : "📞"}{" "}
            {c.caller_name ?? c.caller_number ?? "非通知/不明"}
          </span>
          {c.active ? (
            <span className="badge-live">
              <span className="pulse-dot" />
              通話中
            </span>
          ) : (
            <span className="muted">{time}</span>
          )}
        </div>
        <div className="card-preview">
          {c.preview.length === 0 && <span className="muted">(まだ発話がありません)</span>}
          {c.preview.map((s) => (
            <div key={s.seq} className={`seg ${s.speaker === "caller" ? "caller" : "ai"}`}>
              <span className="speaker">{speakerLabel(s.speaker)}</span>
              {s.text}
            </div>
          ))}
        </div>
      </div>
    </Link>
  );
}

// 発信キーパッド (2026-07-19): 番号を入力して自分がかける。AIは書記モードで支援
function DialPad() {
  const { dial, dialing } = useDialer();
  const [open, setOpen] = useState(false);
  const [num, setNum] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const busy = dialing?.status === "dialing" || dialing?.status === "answered";

  const start = async () => {
    const number = num.replace(/[-\s()]/g, "");
    if (!number) return;
    if (!window.confirm(`${number} に発信します (通話料がかかります)。よろしいですか？`)) {
      return;
    }
    const e = await dial(number);
    setErr(e);
    if (!e) {
      setNum("");
      setOpen(false);
    }
  };

  const keys = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "*", "0", "#"];
  return (
    <div className="dialpad-wrap">
      <button onClick={() => setOpen((o) => !o)} title="番号を入力して発信します">
        📞 発信
      </button>
      {open && (
        <div className="dialpad panel">
          <input
            autoFocus
            value={num}
            inputMode="tel"
            placeholder="電話番号 (例: 0801234〜)"
            onChange={(e) => setNum(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") start();
            }}
          />
          <div className="dialpad-keys">
            {keys.map((k) => (
              <button key={k} onClick={() => setNum((n) => n + k)}>
                {k}
              </button>
            ))}
          </div>
          <div className="dialpad-actions">
            <button onClick={() => setNum((n) => n.slice(0, -1))} title="1文字削除">
              ⌫
            </button>
            <button className="dial-go" onClick={start} disabled={busy || !num.trim()}>
              📞 発信する
            </button>
          </div>
          {err && <div className="muted">⚠ {err}</div>}
        </div>
      )}
    </div>
  );
}

export default function Home() {
  const [calls, setCalls] = useState<CallItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const res = await fetch("/api/calls?limit=12", { cache: "no-store" });
        if (!res.ok) throw new Error(`DBに接続できません (${res.status})`);
        const data = await res.json();
        if (alive) {
          setCalls(data);
          setError(null);
        }
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    poll();
    const id = setInterval(poll, 2000); // 通話中カードのプレビューは発話単位でリアルタイム更新
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const active = calls?.filter((c) => c.active) ?? [];
  const past = calls?.filter((c) => !c.active) ?? [];

  return (
    <>
      {/* ⚠操作類を無名の div で包まないこと (2026-09-18)。スマホ用CSSは `order` で
          この3つを並べ替えるが、**order は直接の子にしか効かない**。包むと375px幅で
          ボタンが縦書きに潰れ、タイトルに重なる。右寄せは .home-actions の
          margin-left:auto が担当するので、space-between も要らない */}
      <div className="toolbar home-head">
        <h1 style={{ margin: 0 }}>管制室</h1>
        <div className="home-actions">
          <DialPad />
          {/* ⚠テスト通話のボタンは置かない (2026-09-24 ユーザー「外部ツールであるべき」)。
              開発の道具は infra/scripts/testcall.sh */}
        </div>
        <AnswerModeControl />
      </div>
      {error && (
        <div className="panel">⚠ {error} — スタック (docker compose) は起動していますか?</div>
      )}
      {active.length > 0 && (
        <>
          <h2 className="section-title">通話中</h2>
          <div className="cards">
            {active.map((c) => (
              <CallCard key={c.id} c={c} />
            ))}
          </div>
        </>
      )}
      {active.length === 0 && !error && (
        <div className="panel muted">
          アクティブな通話はありません。050に架電するとここにカードが現れます。
        </div>
      )}
      {past.length > 0 && (
        <>
          {/* ⚠スマホでは3件までに切る (CSS)。トップが履歴で埋まって、上の応答モードと
              通話中が下へ押し出されるのを防ぐため。全件は履歴タブが受け持つ */}
          <div className="section-head">
            <h2 className="section-title">最近の通話</h2>
            <Link className="recent-more" href="/history">
              すべて見る →
            </Link>
          </div>
          <div className="cards home-recent">
            {past.map((c) => (
              <CallCard key={c.id} c={c} />
            ))}
          </div>
        </>
      )}
    </>
  );
}
