"use client";

import { useEffect, useState } from "react";

import { isStaleBuildError, reloadIfStale } from "@/lib/staleBuild";

// エラー境界の中身 (app/error.tsx と app/global-error.tsx で共用)。
//
// ⚠管制室が死んだまま放置されると着信を取り逃すので、
//   「何が起きたか」より**復帰の導線**を優先して出す。
export default function ErrorScreen({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  // 古いビルドが原因なら自動で再読み込みする。描画は一瞬で消えるので何も出さない。
  const [reloading, setReloading] = useState(false);

  useEffect(() => {
    if (reloadIfStale(error)) setReloading(true);
  }, [error]);

  if (reloading) return null;

  // 自動再読み込みを試したのに、まだ古いビルド由来のエラーが出ている状態。
  const staleButStuck = isStaleBuildError(error);

  return (
    <div className="errscreen">
      <h1 className="page-title">管制室を表示できませんでした</h1>
      <p className="errscreen-lead">
        {staleButStuck ? (
          <>
            新しい版が配備された直後のようです。
            <b>再読み込み</b>しても直らない場合は、ブラウザのキャッシュを無視した再読み込み
            (<code>Ctrl</code> + <code>Shift</code> + <code>R</code>) を試してください。
          </>
        ) : (
          <>
            画面の描画中に想定外のエラーが起きました。通話そのもの (電話の受け答えと録音) は
            サーバー側で動いているので、<b>この画面が落ちても着信の応答は止まりません</b>。
          </>
        )}
      </p>

      <div className="errscreen-actions">
        {/* 素の button が主ボタンのスタイル (globals.css) */}
        <button onClick={() => reset()}>もう一度試す</button>
        <button className="btn-quiet" onClick={() => window.location.reload()}>
          再読み込み
        </button>
      </div>

      <details className="errscreen-detail">
        <summary>エラーの詳細</summary>
        <pre>
          {error.name}: {error.message}
          {error.digest ? `\n(digest: ${error.digest})` : ""}
        </pre>
      </details>
    </div>
  );
}
