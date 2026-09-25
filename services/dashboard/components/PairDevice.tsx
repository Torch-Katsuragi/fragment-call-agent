"use client";

import { useState } from "react";
import QRCode from "qrcode";

// スマホアプリ (専用電話アプリ) のペアリング。
//
// QRには接続先URLと端末トークンの両方を入れる — 中央の振り分けサーバーを作らない
// 代わりに、URLをQRで配る設計 (顧客ごとにVMが違うシングルテナント構成でも、
// セルフホストでも、同じ導線になる)。
//
// ⚠このトークンは管制室全体に入れる鍵。QRを他人に見せない・スクショを残さない。
//   失効させるには .env の DEVICE_TOKEN_VERSION を変えて dashboard を再起動する
//   (発行済みトークンが全部無効になる)。
export default function PairDevice() {
  const [name, setName] = useState("スマホ");
  const [qr, setQr] = useState<string | null>(null);
  const [manual, setManual] = useState<{ url: string; token: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const issue = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(
        `/api/device/pair?name=${encodeURIComponent(name.trim() || "スマホ")}`,
        { cache: "no-store" },
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json(); // { url, token, name }
      setManual({ url: data.url, token: data.token });
      setQr(
        await QRCode.toDataURL(JSON.stringify(data), {
          errorCorrectionLevel: "M",
          margin: 2,
          width: 280,
        }),
      );
    } catch (e) {
      setError(`発行に失敗しました (${e instanceof Error ? e.message : e})`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="set-row">
        <div className="set-row-text">
          <div className="set-row-title">端末を追加</div>
          <div className="set-row-desc">アプリでQRを読むと設定が終わります</div>
        </div>
        <div className="pair-ctl">
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="端末の名前"
            aria-label="端末の名前"
          />
          <button onClick={issue} disabled={busy}>
            {busy ? "発行中…" : qr ? "発行し直す" : "QRを表示"}
          </button>
        </div>
      </div>
      {(error || qr) && (
        <div className="set-sub">
          {error && <div className="set-row-desc" style={{ color: "#e5484d" }}>{error}</div>}
          {qr && (
            <>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={qr} alt="ペアリングQR" width={240} height={240} className="pair-qr" />
              <div className="set-row-desc">⚠ QRは鍵です。人に見せないでください</div>
              {manual && (
                <details style={{ marginTop: 6 }}>
                  <summary className="set-row-desc">読めないとき (手入力)</summary>
                  <div className="set-row-desc" style={{ userSelect: "all", wordBreak: "break-all" }}>
                    URL: <code>{manual.url}</code>
                    <br />
                    トークン: <code>{manual.token}</code>
                  </div>
                </details>
              )}
            </>
          )}
        </div>
      )}
    </>
  );
}
