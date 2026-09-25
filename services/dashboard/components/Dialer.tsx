"use client";

import { createContext, useContext, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useCallSession } from "@/components/CallSession";

// 発信 (2026-07-19): 管制室から自分がかける。話すのは本人 — AIは書記モードで
// 文字起こし+吹き出し支援に回る。相手が出ると受話フローと同じ機構で
// 通話画面 (?op=1 = マイクON) へ自動遷移する

type DialState = {
  number: string;
  status: "dialing" | "answered" | "failed";
  since: number;
  reason: string;
} | null;

type Ctx = {
  /** 発信する。エラーメッセージを返す (null = 受理された) */
  dial: (number: string) => Promise<string | null>;
  dialing: DialState;
};

const DialerCtx = createContext<Ctx>({ dial: async () => null, dialing: null });
export const useDialer = () => useContext(DialerCtx);

export default function DialerProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { activeCall } = useCallSession();
  const [dialing, setDialing] = useState<DialState>(null);
  const watching = useRef<string | null>(null); // 発信後、この番号の通話出現を見張る
  const failedAt = useRef<number | null>(null);

  useEffect(() => {
    let alive = true;
    const poll = async () => {
      try {
        const res = await fetch("/api/dial", { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        if (alive) setDialing(data.dialing);
      } catch {
        /* hookd停止中はバナーなし */
      }
    };
    poll();
    const t = setInterval(poll, 1500);
    return () => {
      alive = false;
      clearInterval(t);
    };
  }, []);

  // 相手が出て通話行が現れたら通話画面へ (operator=1 で最初からマイクON)
  useEffect(() => {
    if (!watching.current || !activeCall) return;
    if (activeCall.caller_number === watching.current) {
      watching.current = null;
      router.push(`/call/${activeCall.id}?op=1`);
    }
  }, [activeCall, router]);

  // 失敗の見張り解除 + 失敗バナーの表示時刻を記録
  useEffect(() => {
    if (dialing?.status === "failed") {
      watching.current = null;
      failedAt.current ??= Date.now();
    } else {
      failedAt.current = null;
    }
  }, [dialing]);

  const dial = async (raw: string): Promise<string | null> => {
    const number = raw.replace(/[-\s()]/g, "");
    try {
      const res = await fetch("/api/dial", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ number }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) return data.error ?? "発信できませんでした";
      watching.current = data.number ?? number;
      setDialing({
        number: data.number ?? number,
        status: "dialing",
        since: Date.now() / 1000,
        reason: "",
      });
      return null;
    } catch (e) {
      return String(e);
    }
  };

  const show =
    dialing !== null &&
    (dialing.status === "dialing" ||
      dialing.status === "answered" ||
      (dialing.status === "failed" &&
        failedAt.current !== null &&
        Date.now() - failedAt.current < 15000));

  return (
    <DialerCtx.Provider value={{ dial, dialing }}>
      {show && dialing && (
        <div className="ringing-banner global">
          {dialing.status === "failed" ? (
            <span>⚠ 発信失敗: {dialing.reason || "接続できませんでした"}</span>
          ) : (
            <>
              <span className="pulse-dot" />
              <span className="ringing-label" title={dialing.number}>
                📞 発信中: {dialing.number}
                {dialing.status === "answered" ? " ・接続中…" : "…"}
              </span>
              <span className="muted">相手が出たら自動で通話画面に移ります (あなたが話します)</span>
            </>
          )}
        </div>
      )}
      {children}
    </DialerCtx.Provider>
  );
}

// 通話詳細 (終了後) の「かけ直す」ボタン
export function RedialButton({ number }: { number: string }) {
  const { dial, dialing } = useDialer();
  const busy = dialing?.status === "dialing" || dialing?.status === "answered";
  return (
    <button
      disabled={busy}
      title="この相手に発信します (あなたが話し、AIは書記で支援)"
      onClick={async () => {
        if (!window.confirm(`${number} に発信します (通話料がかかります)。よろしいですか？`)) {
          return;
        }
        const err = await dial(number);
        if (err) window.alert(`発信できませんでした: ${err}`);
      }}
    >
      📞 かけ直す
    </button>
  );
}
