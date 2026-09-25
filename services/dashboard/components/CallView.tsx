"use client";

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import Link from "next/link";
import MonitorVolume from "@/components/MonitorVolume";
import {
  StartAudio,
  useIsSpeaking,
  useLocalParticipant,
  useMediaDeviceSelect,
  useTranscriptions,
} from "@livekit/components-react";
import { useCallSession } from "@/components/CallSession";
import { RedialButton } from "@/components/Dialer";

type Segment = { seq: number; speaker: string; text: string };
type Fragment = { id: number; kind: string; title: string; text: string; created_at: string };
type CallDetail = {
  id: string;
  caller_number: string | null;
  caller_name?: string | null;
  room_name: string;
  started_at: string;
  ended_at: string | null;
  direction?: string;
  segments: Segment[];
  fragments?: Fragment[];
};
// 通話詳細 = web会議風レイアウト。
// 中央: フラグメント (エージェントの思考が主役) + 下部バー、右: 会話ストリーム (全高固定レール)。
// レール下端の入力からAIへ「耳打ち」できる (相手には聞こえない)。
// LiveKit接続はCallSessionProvider (グローバル) が持つ — この画面は表示と操作だけ
export default function CallView({
  id,
  initialOperator = false,
}: {
  id: string;
  initialOperator?: boolean;
}) {
  const cs = useCallSession();
  const [call, setCall] = useState<CallDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  // 送信済みでまだDBに現れていない耳打ち (楽観表示)
  const [pendingWhispers, setPendingWhispers] = useState<string[]>([]);

  const active = call?.ended_at === null;
  // この通話がグローバル接続中のアクティブ通話か (LiveKitフックはこの時だけ使える)
  const isLive = !!active && cs.connected && cs.activeCall?.id === id;

  // 受話ボタン経由 (?op=1) は最初からoperator (マイクON) で入る
  useEffect(() => {
    if (initialOperator) cs.requestOperator();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialOperator]);

  useEffect(() => {
    let alive = true;
    let timer: ReturnType<typeof setInterval> | null = null;
    const poll = async () => {
      try {
        const res = await fetch(`/api/calls/${id}`, { cache: "no-store" });
        if (!res.ok) throw new Error(`通話情報を取得できません (${res.status})`);
        const data: CallDetail = await res.json();
        if (!alive) return;
        setCall(data);
        setError(null);
        // DBに反映された耳打ちは楽観表示から下ろす
        setPendingWhispers((prev) =>
          prev.filter(
            (t) => !data.segments.some((s) => s.speaker === "whisper" && s.text === t),
          ),
        );
        if (data.ended_at !== null && timer) {
          clearInterval(timer); // 終了した通話はポーリング不要
        }
      } catch (e) {
        if (alive) setError(String(e));
      }
    };
    poll();
    timer = setInterval(poll, 1500);
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [id]);

  if (error) return <div className="panel">⚠ {error}</div>;
  if (!call) return <div className="panel muted">読み込み中…</div>;

  const caller = call.caller_number ?? "非通知/不明";
  const started = new Date(call.started_at).toLocaleString("ja-JP");
  const dur = call.ended_at
    ? Math.round((new Date(call.ended_at).getTime() - new Date(call.started_at).getTime()) / 1000)
    : null;

  const hangup = async () => {
    if (!window.confirm("通話を切断しますか？ (回線ごと切れます)")) return;
    try {
      await fetch(`/api/calls/${id}/hangup`, { method: "POST" });
    } catch {
      /* 失敗時はDBポーリングが現状を映すのでそのまま */
    }
  };

  const sendWhisper = async (text: string) => {
    setPendingWhispers((prev) => [...prev, text]);
    try {
      const res = await fetch(`/api/calls/${id}/whisper`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!res.ok) throw new Error();
    } catch {
      setPendingWhispers((prev) => prev.filter((t) => t !== text));
      window.alert("耳打ちを送信できませんでした");
    }
  };

  const header = (
    <div className="toolbar">
      <Link href="/">← 管制室</Link>
      <h1 style={{ margin: 0 }}>
        {call.direction === "outbound" ? "↗" : "📞"} {call.caller_name ?? caller}
      </h1>
      {call.caller_name && <span className="muted">{caller}</span>}
      {call.direction === "outbound" && <span className="muted">発信</span>}
      {active ? (
        <span className="badge-live">
          <span className="pulse-dot" />
          通話中
          <Elapsed since={call.started_at} />
        </span>
      ) : (
        <span className="muted">
          {started}
          {dur !== null && ` ・ ${Math.floor(dur / 60)}分${dur % 60}秒`} ・ 終了
        </span>
      )}
      {!active && call.caller_number && <RedialButton number={call.caller_number} />}
    </div>
  );

  const rail = (interim?: React.ReactNode) => (
    <ChatRail
      segments={call.segments}
      pendingWhispers={pendingWhispers}
      interim={interim}
      active={!!active}
      onSend={sendWhisper}
    />
  );

  return (
    <>
      <div className="call-main">
        {header}
        <FragmentsPanel items={call.fragments ?? []} />
        {isLive && (
          <CallBar operator={cs.operator} onSwitch={cs.toggleOperator} onHangup={hangup} />
        )}
      </div>
      {isLive ? <InterimAware render={rail} /> : rail()}
    </>
  );
}

// 経過時間 (mm:ss)
function Elapsed({ since }: { since: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);
  const s = Math.max(0, Math.floor((now - new Date(since).getTime()) / 1000));
  return (
    <span className="elapsed">
      {Math.floor(s / 60)}:{String(s % 60).padStart(2, "0")}
    </span>
  );
}

// web会議風の下部バー: デバイス選択・AIスイッチ・発話インジケータ・通話終了
function CallBar({
  operator,
  onSwitch,
  onHangup,
}: {
  operator: boolean;
  onSwitch: () => void;
  onHangup: () => void;
}) {
  const { localParticipant } = useLocalParticipant();
  const speaking = useIsSpeaking(localParticipant);
  const mic = useMediaDeviceSelect({ kind: "audioinput" });
  const spk = useMediaDeviceSelect({ kind: "audiooutput" });

  return (
    <div className="call-bar">
      <StartAudio label="🔊 音声オン" />
      <MonitorVolume compact />
      <label className="devsel" title="マイク (自分の声の入力)">
        🎤
        <select
          value={mic.activeDeviceId}
          onChange={(e) => mic.setActiveMediaDevice(e.target.value)}
        >
          {mic.devices.map((d) => (
            <option key={d.deviceId} value={d.deviceId}>
              {d.label || "マイク"}
            </option>
          ))}
        </select>
      </label>
      <label className="devsel" title="スピーカー (通話音声の出力先)">
        🔊
        <select
          value={spk.activeDeviceId}
          onChange={(e) => spk.setActiveMediaDevice(e.target.value)}
        >
          {spk.devices.map((d) => (
            <option key={d.deviceId} value={d.deviceId}>
              {d.label || "スピーカー"}
            </option>
          ))}
        </select>
      </label>
      {operator && (
        <span
          className={`speak-dot ${speaking ? "on" : ""}`}
          title="自分の声を拾えていれば話すたびに光ります"
        />
      )}
      <button className={`bar-btn switch ${operator ? "to-ai" : "to-me"}`} onClick={onSwitch}>
        {operator ? "🤖 AIに任せる" : "🎙 自分が出る"}
      </button>
      <div className="bar-spacer" />
      <button className="bar-btn danger" onClick={onHangup}>
        📞 通話終了
      </button>
    </div>
  );
}

// LINE風の吹き出し1件。相手=左・白 / AI=右・濃グレー / 本人=右・緑 / 耳打ち=右・薄緑点線
function Msg({
  speaker,
  text,
  interim,
}: {
  speaker: string;
  text: string;
  interim?: boolean;
}) {
  const who =
    speaker === "caller"
      ? "caller"
      : speaker === "user"
        ? "user"
        : speaker === "whisper"
          ? "whisper"
          : "ai";
  const side = who === "caller" ? "left" : "right";
  const tag =
    who === "user"
      ? "あなた"
      : who === "whisper"
        ? "🤫 耳打ち (相手には聞こえません)"
        : who === "ai"
          ? "AI"
          : null;
  return (
    <div className={`msg ${side} ${who} ${interim ? "interim" : ""}`}>
      <div className="msg-inner">
        {tag && <div className="msg-tag">{tag}</div>}
        <div className="msg-bubble">{text}</div>
      </div>
    </div>
  );
}

// LiveKitRoomコンテキスト内で interim (発話途中・未確定) セグメントを拾って渡す
function InterimAware({ render }: { render: (interim?: React.ReactNode) => React.ReactNode }) {
  const transcriptions = useTranscriptions();
  const interim = transcriptions
    .filter((t) => t.streamInfo?.attributes?.["lk.transcription_final"] !== "true")
    .map((t, i) => {
      const isCaller = (t.participantInfo?.identity ?? "").startsWith("sip_");
      return (
        <Msg
          key={t.streamInfo?.id ?? i}
          speaker={isCaller ? "caller" : "ai"}
          text={t.text}
          interim
        />
      );
    });
  return <>{render(interim.length > 0 ? interim : undefined)}</>;
}

// 右側の全高固定レール: 会話ストリーム + 耳打ち入力
function ChatRail({
  segments,
  pendingWhispers,
  interim,
  active,
  onSend,
}: {
  segments: Segment[];
  pendingWhispers: string[];
  interim?: React.ReactNode;
  active: boolean;
  onSend: (text: string) => void;
}) {
  const [draft, setDraft] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const last = segments[segments.length - 1];

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [segments.length, last?.text, interim, pendingWhispers.length]);

  const submit = (e: React.FormEvent) => {
    e.preventDefault();
    const text = draft.trim();
    if (!text || !active) return;
    onSend(text);
    setDraft("");
  };

  return (
    <div className="chat-rail">
      <div className="chat-rail-head">💬 会話ストリーム</div>
      <div className="chat-stream">
        {segments.length === 0 && !interim && (
          <span className="muted">まだ発話がありません。</span>
        )}
        {segments.map((s) => (
          <Msg key={s.seq} speaker={s.speaker} text={s.text} />
        ))}
        {pendingWhispers.map((t, i) => (
          <Msg key={`pw-${i}`} speaker="whisper" text={t} interim />
        ))}
        {interim}
        <div ref={bottomRef} />
      </div>
      <form className="chat-input" onSubmit={submit}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={active ? "AIへ耳打ち (相手には聞こえません)" : "通話は終了しています"}
          disabled={!active}
          maxLength={500}
        />
        <button type="submit" disabled={!active || !draft.trim()}>
          送信
        </button>
      </form>
    </div>
  );
}

// フラグメント (思考の断片): 会話監視エージェントが浮かべる吹き出し。本人専用画面なので
// 【本人限定】情報も表示される。
// word cloud風の物理配置 — 吹き出しは互いに押し合いへし合いして無秩序に並ぶ。
// 通常はtitleだけを表示し、クリックで膨らんで (他を押しのけて) textを表示する
const KIND_ICON: Record<string, string> = { info: "💡", alert: "⚠️", hint: "🧭" };

// idベースの決定的疑似乱数 (再レンダリングで位置が飛ばないように)
const seeded = (seed: number) => {
  const x = Math.sin(seed) * 10000;
  return x - Math.floor(x);
};

function FragmentsPanel({ items }: { items: Fragment[] }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const nodeRefs = useRef(new Map<number, HTMLDivElement>());
  const posRef = useRef(new Map<number, { x: number; y: number }>());
  const [expanded, setExpanded] = useState<Set<number>>(new Set());
  const [positions, setPositions] = useState<Map<number, { x: number; y: number }>>(new Map());

  // サイズ計測 → 反復緩和 (重なり分離 + 中心への弱い引力 + 境界クランプ) → 位置確定。
  // 位置はCSS transitionで滑らかに動く = 押し合いへし合いのアニメーション。
  // useLayoutEffect: 初回レンダリング直後 (描画前) に計測しないと、次のポーリングまで
  // 全部が中央に重なったまま見えてしまう
  useLayoutEffect(() => {
    const cont = containerRef.current;
    if (!cont) return;
    const W = cont.clientWidth;
    const H = cont.clientHeight;
    if (W === 0 || H === 0) return;
    const MARGIN = 10;
    const nodes = items.map((f) => {
      const el = nodeRefs.current.get(f.id);
      const w = (el?.offsetWidth ?? 140) + MARGIN;
      const h = (el?.offsetHeight ?? 44) + MARGIN;
      const p =
        posRef.current.get(f.id) ??
        ({
          x: W / 2 + (seeded(f.id * 7.31) - 0.5) * W * 0.6,
          y: H / 2 + (seeded(f.id * 13.7) - 0.5) * H * 0.6,
        } as { x: number; y: number });
      return { id: f.id, x: p.x, y: p.y, w, h };
    });
    for (let iter = 0; iter < 180; iter++) {
      let moved = false;
      // 仕上げフェーズ (最後の50回) は中心引力を切り、重なり解消だけを収束させる
      const settle = iter >= 130;
      if (!settle) {
        for (const n of nodes) {
          n.x += (W / 2 - n.x) * 0.012;
          n.y += (H / 2 - n.y) * 0.012;
        }
      }
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i];
          const b = nodes[j];
          const dx = a.x - b.x;
          const dy = a.y - b.y;
          const ox = (a.w + b.w) / 2 - Math.abs(dx);
          const oy = (a.h + b.h) / 2 - Math.abs(dy);
          if (ox > 0 && oy > 0) {
            moved = true;
            // 主軸 = 重なりの小さい軸で押し分ける。ただし壁クランプで主軸が詰むことが
            // あるので、副軸にも少し逃がす (詰んだペアは反復のうちに縦/横へ回り込む)
            const s2 = (Math.min(ox, oy) * 0.25) / 2;
            if (ox < oy) {
              const s = ((dx >= 0 ? 1 : -1) * ox) / 2;
              a.x += s;
              b.x -= s;
              a.y += (dy >= 0 ? 1 : -1) * s2;
              b.y -= (dy >= 0 ? 1 : -1) * s2;
            } else {
              const s = ((dy >= 0 ? 1 : -1) * oy) / 2;
              a.y += s;
              b.y -= s;
              a.x += (dx >= 0 ? 1 : -1) * s2;
              b.x -= (dx >= 0 ? 1 : -1) * s2;
            }
          }
        }
      }
      for (const n of nodes) {
        n.x = Math.min(W - n.w / 2 - 4, Math.max(n.w / 2 + 4, n.x));
        n.y = Math.min(H - n.h / 2 - 4, Math.max(n.h / 2 + 4, n.y));
      }
      if (!moved && (settle || iter > 20)) break;
    }
    const next = new Map(nodes.map((n) => [n.id, { x: n.x, y: n.y }]));
    posRef.current = next;
    setPositions(next);
    // 展開/新規で吹き出しサイズが変わった直後にもう一度呼ばれるよう、依存に expanded を含む
  }, [items, expanded]);

  const toggle = (id: number) =>
    setExpanded((prev) => {
      const s = new Set(prev);
      if (s.has(id)) s.delete(id);
      else s.add(id);
      return s;
    });

  return (
    <div className="panel fragments-cloud" ref={containerRef}>
      <div className="fragments-title">🧩 フラグメント</div>
      {items.length === 0 && (
        <span className="muted cloud-empty">エージェントの気づきがここに浮かびます。</span>
      )}
      {items.map((f) => {
        const p = positions.get(f.id);
        const open = expanded.has(f.id);
        return (
          <div
            key={f.id}
            ref={(el) => {
              if (el) nodeRefs.current.set(f.id, el);
              else nodeRefs.current.delete(f.id);
            }}
            className={`fbubble ${f.kind} ${open ? "open" : ""} ${p ? "" : "measuring"}`}
            style={p ? { left: p.x, top: p.y } : { left: "50%", top: "50%" }}
            onClick={() => toggle(f.id)}
            title={open ? "クリックで閉じる" : f.text}
          >
            <span className="fbubble-icon">{KIND_ICON[f.kind] ?? "🧩"}</span>
            {open ? f.text : f.title || f.text.slice(0, 18)}
          </div>
        );
      })}
    </div>
  );
}
