"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  LiveKitRoom,
  StartAudio,
  useTranscriptions,
} from "@livekit/components-react";
import MonitorAudio from "@/components/MonitorAudio";
import MonitorVolume from "@/components/MonitorVolume";

type Conn = { token: string; url: string };

export default function RoomView({ room, title }: { room: string; title?: string }) {
  const [conn, setConn] = useState<Conn | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch(`/api/token?room=${encodeURIComponent(room)}`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`token API ${r.status}`))))
      .then(setConn)
      .catch((e) => setError(String(e)));
  }, [room]);

  return (
    <>
      <div className="toolbar">
        <Link href="/">← 管制室</Link>
        <h1 style={{ margin: 0 }}>{title ?? `📞 ${room}`}</h1>
        <span className="badge-live">
          <span className="pulse-dot" />
          通話中
        </span>
      </div>
      {error && <div className="panel">⚠ {error}</div>}
      {!conn && !error && <div className="panel muted">接続中…</div>}
      {conn && (
        <LiveKitRoom
          serverUrl={conn.url}
          token={conn.token}
          connect
          audio={false}
          video={false}
        >
          <div className="toolbar">
            {/* ブラウザの自動再生制限があるため、モニタ音声はボタンで開始する */}
            <StartAudio label="🔊 通話音声をモニタする" />
            <MonitorVolume compact />
            <span className="muted">音声はモニタのみ (交代機能は M3)</span>
          </div>
          <MonitorAudio />
          <Transcript />
        </LiveKitRoom>
      )}
    </>
  );
}

function Transcript() {
  const transcriptions = useTranscriptions();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [transcriptions.length, transcriptions[transcriptions.length - 1]?.text]);

  return (
    <div className="panel transcript">
      {transcriptions.length === 0 && (
        <span className="muted">まだ発話がありません。話し始めるとここに流れます。</span>
      )}
      {transcriptions.map((t, i) => {
        const identity = t.participantInfo?.identity ?? "?";
        const isCaller = identity.startsWith("sip_");
        const isFinal = t.streamInfo?.attributes?.["lk.transcription_final"] === "true";
        const cls = `seg ${isCaller ? "caller" : "ai"} ${isFinal ? "" : "interim"}`;
        return (
          <div key={t.streamInfo?.id ?? i} className={cls}>
            <span className="speaker">{isCaller ? "発信者" : "AI"}</span>
            {t.text}
          </div>
        );
      })}
      <div ref={bottomRef} />
    </div>
  );
}
