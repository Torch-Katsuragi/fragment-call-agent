"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useCallSession } from "@/components/CallSession";

type Ringing = {
  number: string;
  since: number;
  pickup: boolean;
  phase?: "announcing" | "ringing";
  mode?: string; // away / standby / manual
  ring_ms?: number; // 本人が取れる残り時間。0 = もう AI に渡った (スタンバイの時間切れ等)
};
type Lookup = { name?: string | null; verdict: string; summary: string };

// 全画面共通の着信バナー + コール音 — どの画面にいても着信に気づける。
// 本人が取れる間 (スタンバイ / 自分で出る) は 切る / AIに任せる / 出る の3択 (2026-09-25、スマホと揃えた)。
// 出るを押すと Asterisk/シミュレータがブリッジし通話画面へ自動遷移
export default function GlobalRinging() {
  const router = useRouter();
  const { activeCall } = useCallSession();
  const [ringing, setRinging] = useState<Ringing | null>(null);
  const [decided, setDecided] = useState<"ai" | "reject" | null>(null);
  const [callerName, setCallerName] = useState<string | null>(null);
  const [lookup, setLookup] = useState<Lookup | null>(null);
  const [picking, setPicking] = useState(false);
  const pickedNumber = useRef<string | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const res = await fetch("/api/ringing", { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        if (!alive) return;
        setRinging(data.ringing);
        setCallerName(data.caller_name ?? null);
        setLookup(data.lookup ?? null);
        if (!data.ringing) {
          setPicking(false);
          setDecided(null);
        }
      } catch {
        /* hookd停止中はバナーなし */
      }
    };
    poll();
    const id = setInterval(poll, 1500);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  // 受話後: その番号のアクティブ通話が現れたら通話画面へ (operator=1 でマイクON)
  useEffect(() => {
    if (!pickedNumber.current || !activeCall) return;
    if (activeCall.caller_number === pickedNumber.current) {
      pickedNumber.current = null;
      router.push(`/call/${activeCall.id}?op=1`);
    }
  }, [activeCall, router]);

  // すでに同番号のアクティブ通話がある = ブリッジ済み → バナー/コール音は不要
  const visible =
    ringing !== null && !(activeCall && activeCall.caller_number === ringing.number);

  // 本人が取れる間か。⚠以前は assistant_enabled だけを見ていて、スタンバイでは受話ボタンが出なかった
  const waiting =
    ringing !== null &&
    (ringing.mode === "standby" || ringing.mode === "manual") &&
    (ringing.ring_ms ?? 0) > 0 &&
    decided === null;
  // 録音告知の最中はバナーだけ出してコール音は鳴らさない (2026-09-25、端末と揃える)
  const audible = visible && waiting && ringing?.phase !== "announcing";

  // コール音 (日本の呼出音風: 384+416Hz、1秒鳴って2秒休み)。
  // 自動再生制限でAudioContextがsuspendedの場合は、次のクリックで解禁される
  useEffect(() => {
    if (!audible) return;
    const ctx = (audioCtxRef.current ??= new AudioContext());
    const resume = () => {
      ctx.resume().catch(() => {});
    };
    resume();
    document.addEventListener("click", resume, { once: true });
    const burst = () => {
      if (ctx.state !== "running") return;
      const g = ctx.createGain();
      g.connect(ctx.destination);
      const t = ctx.currentTime;
      g.gain.setValueAtTime(0, t);
      g.gain.linearRampToValueAtTime(0.05, t + 0.02);
      g.gain.setValueAtTime(0.05, t + 0.95);
      g.gain.linearRampToValueAtTime(0, t + 1);
      for (const f of [384, 416]) {
        const o = ctx.createOscillator();
        o.frequency.value = f;
        o.connect(g);
        o.start(t);
        o.stop(t + 1.05);
      }
    };
    burst();
    const iv = setInterval(burst, 3000);
    return () => {
      clearInterval(iv);
      document.removeEventListener("click", resume);
    };
  }, [audible]);

  if (!visible || !ringing) return null;

  const decide = async (action: "ai" | "reject") => {
    setDecided(action);
    try {
      // 端末と同じ口 (hookd /ringing_decide への中継)。管制室のログインでも通る
      await fetch("/api/device/decide", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ number: ringing.number, action }),
      });
    } catch {
      setDecided(null);
    }
  };

  const verdictBadge =
    lookup && (lookup.verdict === "sales" || lookup.verdict === "scam") ? (
      <span className="verdict-badge bad">
        ⚠ {lookup.verdict === "scam" ? "詐欺報告あり" : "営業の可能性"}
      </span>
    ) : null;

  return (
    <div className="ringing-banner global">
      <span className="pulse-dot" />
      <span className="ringing-label" title={ringing.number}>
        着信中: {callerName ??
          (lookup?.name ? `${lookup.name} (${ringing.number})` : ringing.number)}
      </span>
      {verdictBadge}
      {lookup?.summary && lookup.summary !== "情報なし" && (
        <span className="muted ringing-lookup" title={lookup.summary}>
          🔎 {lookup.summary}
        </span>
      )}
      {decided === "ai" ? (
        <span className="muted">AIに渡しました</span>
      ) : decided === "reject" ? (
        <span className="muted">切りました</span>
      ) : !waiting ? (
        <span className="muted">AIが応答します</span>
      ) : picking || ringing.pickup ? (
        <span className="muted">接続中… つながったら自動で通話画面に移ります</span>
      ) : (
        <>
          {/* 切る = 相手ごと切る (AI にも回さない) / AIに任せる = 待たずに AI (2026-09-25) */}
          <button className="btn-danger" onClick={() => decide("reject")}>切る</button>
          <button className="btn-quiet" onClick={() => decide("ai")}>AIに任せる</button>
          {ringing.phase === "announcing" ? (
            // 録音告知の最中。hookd も受話を 409 で断る
            <span className="muted">録音告知中…</span>
          ) : (
            <button
              onClick={async () => {
                setPicking(true);
                pickedNumber.current = ringing.number;
                try {
                  await fetch("/api/pickup", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ number: ringing.number }),
                  });
                } catch {
                  setPicking(false);
                  pickedNumber.current = null;
                }
              }}
            >
              📞 出る
            </button>
          )}
        </>
      )}
    </div>
  );
}
