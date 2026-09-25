"use client";

import { useEffect, useRef, useState } from "react";
import { useRemoteParticipants } from "@livekit/components-react";
import type { Participant } from "livekit-client";

import { useCallSession } from "@/components/CallSession";

// AI が応対している間、スマホのロック画面の上に出す軽量表示 (2026-09-19)。
//
// ユーザーの指示で 3 パターン (端末側の設定で選ぶ):
//   visualizer — 相手の名前 + 音に合わせて伸び縮みする円状のバー
//   chat       — 会話ストリームだけの吹き出し
//   (none はアプリ側でこの画面を開かない)
//
// ⚠管制室の他の画面と違い、操作は何も置かない (耳打ち・交代・切断は無し)。
//   ロック画面に出るものなので、見せるのは「いま何が起きているか」だけ。
// ⚠音を流すかは lib/monitorPolicy.ts (CallSession) が決める。波形はルームの音声トラックを
//   解析するだけなので、音を出していなくても動く。
// ⚠背景は黒固定。ロック画面の上で白い面が光ると眩しい (夜間の着信を想定)。

type Mode = "visualizer" | "chat";

export default function LiveView({ id, mode, debug = false }: { id: string; mode: Mode; debug?: boolean }) {
  const cs = useCallSession();
  const isLive = cs.connected && cs.activeCall?.id === id;
  const name = cs.activeCall?.caller_name ?? cs.activeCall?.caller_number ?? "";

  return (
    <div style={styles.root}>
      {mode === "visualizer" ? (
        <VisualizerPane live={isLive} name={name} />
      ) : (
        <ChatPane id={id} live={isLive} name={name} />
      )}
      {debug && cs.connected && <DebugLevels />}
    </div>
  );
}

// ---------- 名前 + 円状のバー ----------

function VisualizerPane({ live, name }: { live: boolean; name: string }) {
  return (
    <div style={styles.center}>
      <div style={styles.name}>{name || "着信"}</div>
      <div style={styles.sub}>{live ? "AIが応対しています" : "通話は終了しました"}</div>
      <div style={{ marginTop: 24 }}>{live ? <Rings /> : <IdleRing />}</div>
    </div>
  );
}

/** ルーム接続中だけ呼ぶ (LiveKit のフックは LiveKitRoom の中でしか使えない) */
/** 切り分け用の小さな表示 (?debug=1 のときだけ)。参加者ごとの isSpeaking / audioLevel を 200ms ごとに出す */
function DebugLevels() {
  const remote = useRemoteParticipants();
  const [txt, setTxt] = useState("");
  useEffect(() => {
    const t = setInterval(() => {
      setTxt(
        remote
          .map((p) => `${p.identity.slice(0, 18)} spk=${p.isSpeaking ? 1 : 0} lv=${p.audioLevel.toFixed(3)} tracks=${p.audioTrackPublications.size}`)
          .join(" | "),
      );
    }, 200);
    return () => clearInterval(t);
  }, [remote]);
  return <div style={{ position: "fixed", left: 8, bottom: 8, fontSize: 11, color: "#666" }}>{txt || "(no remote)"}</div>;
}

function Rings() {
  // 相手 (SIP) と AI (agent)。operator (本人) は除く。
  // ⚠テスト通話は相手役も "agent-" なので外環 (相手) が出ない。実回線の相手は SIP 参加者
  //   (identity が agent-/operator- 以外) なので両方出る
  const remote = useRemoteParticipants();
  // ⚠音声トラックを持つ参加者だけを候補にする (2026-09-19)。ルームには管制室のモニタ
  //   ("dashboard-…"、音声なし) も居て、最初はそれを相手と誤認して外環が動かなかった。
  //   相手 = agent-/operator- 以外 (実回線なら SIP 参加者)。テスト通話は相手役も agent- なので、
  //   agent- が 2 人いれば 2 人目を相手として扱う (どちらが AI かは区別しない)
  const withAudio = remote.filter((p) => p.audioTrackPublications.size > 0);
  const agents = withAudio.filter((p) => p.identity.startsWith("agent-"));
  const others = withAudio.filter(
    (p) => !p.identity.startsWith("agent-") && !p.identity.startsWith("operator-"),
  );
  const agent = agents[0];
  const caller = others[0] ?? agents[1];
  return (
    <svg width={280} height={280} viewBox="-140 -140 280 280" aria-label="音の波形">
      <circle r={62} fill="none" stroke="#333" strokeWidth={1} />
      {caller && <Ring participant={caller} inner={70} maxLen={48} color="#5ec8ff" bands={36} />}
      {agent && <Ring participant={agent} inner={40} maxLen={24} color="#9be29b" bands={24} />}
    </svg>
  );
}

function IdleRing() {
  return (
    <svg width={280} height={280} viewBox="-140 -140 280 280">
      <circle r={62} fill="none" stroke="#333" strokeWidth={1} />
    </svg>
  );
}

/**
 * 参加者の音量 (participant.audioLevel、0..1) で円周のバーを伸縮させる。
 *
 * ⚠Web Audio (AnalyserNode) は使わない (2026-09-19)。ライブラリの useMultibandTrackVolume も
 *   自前の AnalyserNode も、PC・WebView とも全バーがゼロのまま動かなかった。
 *   audioLevel は LiveKit のサーバーが話者検知で配ってくる値で、音声の再生経路に依存しない。
 *   帯域の情報は無いので、バーごとの揺れは位相をずらした正弦で付けている (見た目の演出)。
 */
function useLevel(participant: Participant): number {
  const [level, setLevel] = useState(0);
  useEffect(() => {
    let cur = 0;
    let raf = 0;
    let last = 0;
    let alive = true;
    const tick = (t: number) => {
      if (!alive) return;
      raf = requestAnimationFrame(tick);
      if (t - last < 40) return;
      last = t;
      const target = participant.isSpeaking ? Math.max(participant.audioLevel, 0.15) : 0;
      // 立ち上がりは速く、減衰はゆっくり
      cur = target > cur ? cur + (target - cur) * 0.5 : cur + (target - cur) * 0.15;
      setLevel(cur);
    };
    raf = requestAnimationFrame(tick);
    return () => {
      alive = false;
      cancelAnimationFrame(raf);
    };
  }, [participant]);
  return level;
}

function Ring({
  participant,
  inner,
  maxLen,
  color,
  bands,
}: {
  participant: Participant;
  inner: number;
  maxLen: number;
  color: string;
  bands: number;
}) {
  const level = useLevel(participant);
  const t = performance.now() / 1000;
  return (
    <g>
      {Array.from({ length: bands }).map((_, i) => {
        const wobble = 0.55 + 0.45 * Math.abs(Math.sin(i * 1.7 + t * 6));
        const len = 4 + Math.min(1, level * 2.5) * maxLen * wobble;
        const angle = (i / bands) * 360;
        return (
          <line
            key={i}
            x1={0}
            y1={-inner}
            x2={0}
            y2={-(inner + len)}
            stroke={color}
            strokeWidth={4}
            strokeLinecap="round"
            transform={`rotate(${angle})`}
          />
        );
      })}
    </g>
  );
}

// ---------- 会話ストリームだけ ----------

type Segment = { seq: number; speaker: string; text: string };

function ChatPane({ id, live, name }: { id: string; live: boolean; name: string }) {
  const [segments, setSegments] = useState<Segment[]>([]);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const res = await fetch(`/api/calls/${id}`, { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        if (!alive) return;
        // 耳打ちは本人向けの裏口なのでここには出さない
        setSegments((data.segments as Segment[]).filter((s) => s.speaker !== "whisper"));
      } catch {
        /* 次回 */
      }
    };
    poll();
    const t = setInterval(poll, 1000);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, [id]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [segments.length]);

  return (
    <div style={styles.chat}>
      <div style={styles.chatHead}>
        <span>{name || "着信"}</span>
        <span style={styles.sub}>{live ? "AIが応対しています" : "通話は終了しました"}</span>
      </div>
      <div style={styles.chatBody}>
        {segments.map((s) => {
          const mine = s.speaker !== "caller";
          const silent = s.text.startsWith("(") && s.text.endsWith(")");
          return (
            <div key={s.seq} style={{ ...styles.row, justifyContent: mine ? "flex-end" : "flex-start" }}>
              <div
                style={{
                  ...styles.bubble,
                  ...(silent ? styles.bubbleSilent : mine ? styles.bubbleAi : styles.bubbleCaller),
                }}
              >
                {s.text}
              </div>
            </div>
          );
        })}
        <div ref={bottom} />
      </div>
    </div>
  );
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    position: "fixed",
    inset: 0,
    background: "#000",
    color: "#eee",
    fontFamily: "system-ui, sans-serif",
    overflow: "hidden",
  },
  center: {
    height: "100%",
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    justifyContent: "center",
    padding: 16,
  },
  name: { fontSize: 28, fontWeight: 600, textAlign: "center" },
  sub: { fontSize: 14, color: "#999", marginTop: 6 },
  chat: { height: "100%", display: "flex", flexDirection: "column" },
  chatHead: {
    padding: "14px 16px",
    borderBottom: "1px solid #222",
    display: "flex",
    flexDirection: "column",
    fontSize: 18,
    fontWeight: 600,
  },
  chatBody: { flex: 1, overflowY: "auto", padding: 12, display: "flex", flexDirection: "column", gap: 8 },
  row: { display: "flex" },
  bubble: { maxWidth: "82%", padding: "8px 12px", borderRadius: 14, fontSize: 16, lineHeight: 1.4 },
  bubbleCaller: { background: "#2a2a2a", color: "#fff", borderBottomLeftRadius: 4 },
  bubbleAi: { background: "#1e3a5f", color: "#e8f2ff", borderBottomRightRadius: 4 },
  bubbleSilent: { background: "transparent", color: "#777", fontSize: 13 },
};
