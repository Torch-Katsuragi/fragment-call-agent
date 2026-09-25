"use client";

import { createContext, useContext, useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { LiveKitRoom, StartAudio } from "@livekit/components-react";
import MonitorAudio from "@/components/MonitorAudio";
import { useMonitorOutsideCall } from "@/lib/monitorPolicy";

// アクティブ通話へのLiveKit接続をアプリ全体で1つ持つプロバイダ。
// どの画面を開いていても通話音声がスピーカーから流れ、operator (マイク発話) も
// 画面遷移で切れない。通話詳細ページはこのcontextを使って交代/退出を操作する
type ActiveCall = {
  id: string;
  room_name: string;
  caller_number: string | null;
  caller_name: string | null;
};

type Ctx = {
  activeCall: ActiveCall | null;
  connected: boolean;
  operator: boolean;
  toggleOperator: () => void;
  requestOperator: () => void;
};

const CallSessionCtx = createContext<Ctx>({
  activeCall: null,
  connected: false,
  operator: false,
  toggleOperator: () => {},
  requestOperator: () => {},
});

export const useCallSession = () => useContext(CallSessionCtx);

export default function CallSessionProvider({ children }: { children: React.ReactNode }) {
  const [activeCall, setActiveCall] = useState<ActiveCall | null>(null);
  const [operator, setOperator] = useState(false);
  const [conn, setConn] = useState<{ token: string; url: string } | null>(null);
  const pathname = usePathname();

  // アクティブ通話の監視 (どの画面でも)
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const res = await fetch("/api/calls?limit=10", { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        if (!alive) return;
        const act = data.find((c: { active: boolean }) => c.active) ?? null;
        setActiveCall((prev) => {
          if (!act) return null;
          if (prev?.id === act.id) return prev; // 参照維持 (無駄な再接続を防ぐ)
          return {
            id: act.id,
            room_name: act.room_name,
            caller_number: act.caller_number,
            caller_name: act.caller_name,
          };
        });
      } catch {
        /* DB未起動時は何もしない */
      }
    };
    poll();
    const t = setInterval(poll, 1000); // 接続開始が挨拶に間に合うよう高速ポーリング
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  // 通話が終わったら接続とoperatorを解除
  useEffect(() => {
    if (!activeCall) {
      setConn(null);
      setOperator(false);
    }
  }, [activeCall]);

  // トークン取得 (operator切替時は取り直して再接続)
  useEffect(() => {
    if (!activeCall || conn) return;
    const role = operator ? "&role=operator" : "";
    fetch(`/api/token?room=${encodeURIComponent(activeCall.room_name)}${role}`, {
      cache: "no-store",
    })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error("token"))))
      .then(setConn)
      .catch(() => {
        /* 次のポーリングで再試行される */
      });
  }, [activeCall, conn, operator]);

  const toggleOperator = () => {
    setConn(null); // 権限の違うトークンで再接続 (operator切断はagent側でhandback検知)
    setOperator((o) => !o);
  };
  const requestOperator = () => {
    if (!operator) {
      setConn(null);
      setOperator(true);
    }
  };

  const ctx: Ctx = { activeCall, connected: !!conn, operator, toggleOperator, requestOperator };
  const onCallPage = activeCall !== null && pathname === `/call/${activeCall.id}`;
  // スマホのロック画面用の軽量表示 (/live/<id>)。チップは出さない。音は設定次第 (audible に含めない)
  const onLivePage = pathname.startsWith("/live/");
  // 音声を流すか (2026-09-18、lib/monitorPolicy.ts の注記)。通話画面では必ず流す。
  // 自分が応対中 (operator) なら画面がどこでも流す — 相手の声が聞こえないと話せない
  const [monitorOutside, setMonitorOutside] = useMonitorOutsideCall();
  const audible = onCallPage || operator || monitorOutside;

  return (
    <CallSessionCtx.Provider value={ctx}>
      {activeCall && conn ? (
        // ⚠key={token}を付けない: keyでの再マウントはページ全体 (children) を作り直して
        // 「何も表示されない時間」を生む。token変更時の再接続はLiveKitRoom自身に任せる
        <LiveKitRoom
          serverUrl={conn.url}
          token={conn.token}
          connect
          audio={operator}
          video={false}
        >
          {/* RoomAudioRendererの置き換え。1.0超の増幅ができるようにするため */}
          <MonitorAudio muted={!audible} />
          {!onCallPage && !onLivePage && (
            <div className="global-call-chip">
              {/* 通話画面の外での音声。⚠StartAudio はブラウザの自動再生制限を解くボタンで、
                  流す/流さないの意思とは別物。意思はこちらのボタン (端末ごとに保存) */}
              {audible ? <StartAudio label="🔊 音声オン" /> : null}
              {!operator && (
                <button
                  className="btn-quiet"
                  onClick={() => setMonitorOutside(!monitorOutside)}
                  title="通話画面を開いていないときも音声を流すか (設定にも同じ項目があります)"
                >
                  {monitorOutside ? "🔊 流している" : "🔇 流さない"}
                </button>
              )}
              <Link href={`/call/${activeCall.id}`}>
                <span className="pulse-dot" /> 通話中:{" "}
                {activeCall.caller_name ?? activeCall.caller_number ?? "不明"}
                {operator ? " ・🎙あなたが応対中" : ""}
              </Link>
            </div>
          )}
          {children}
        </LiveKitRoom>
      ) : (
        children
      )}
    </CallSessionCtx.Provider>
  );
}
