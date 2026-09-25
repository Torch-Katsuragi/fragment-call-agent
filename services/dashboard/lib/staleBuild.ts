"use client";

// 配備で古くなったタブの自己回復 (2026-07-31)。
//
// ⚠なぜ必要か: Next.jsは**ビルドごとにJSのファイル名 (ビルドID) が変わる**ので、
//   デプロイをまたいで開きっぱなしのタブは、もう存在しないJSを取りに行って404になる。
//   すると `ChunkLoadError` が飛び、エラー境界が無ければ素の
//   「Application error: a client-side exception has occurred」で全画面が死ぬ。
//
// ⚠これは管制室では特に重い: **着信を待つために開きっぱなしにする画面**なので、
//   死んだことに気づかないまま着信を取り逃す。実際に2026-07-31の配備で踏んだ。
//   (13:35に撤回版をビルド → それ以前から開いていたタブが死亡)
//
// 対処は単純で、古いビルドが原因なら**黙って一度だけ再読み込みする**。
// 再読み込みすれば新しいビルドIDのJSを取り直すので確実に直る。

const RELOAD_KEY = "fragment.staleReloadAt";
// ⚠ループ防止の窓。再読み込みしても直らない (=本当のバグ) 場合に無限リロードすると
//   何が起きているか分からなくなるので、この時間内の2回目はリロードせず画面を出す。
const RELOAD_COOLDOWN_MS = 30_000;

/** 古いビルドのJSが取れなかったことによるエラーか。 */
export function isStaleBuildError(err: unknown): boolean {
  if (!err) return false;
  const e = err as { name?: string; message?: string };
  if (e.name === "ChunkLoadError") return true;
  const msg = String(e.message ?? err);
  return (
    /Loading (CSS )?chunk [\w-]+ failed/i.test(msg) ||
    /Failed to fetch dynamically imported module/i.test(msg) ||
    /error loading dynamically imported module/i.test(msg) ||
    /Importing a module script failed/i.test(msg)
  );
}

/**
 * 古いビルドが原因なら一度だけ再読み込みする。
 * @returns 再読み込みを開始したら true (呼び出し側はそのまま何も描かなくてよい)
 */
export function reloadIfStale(err: unknown): boolean {
  if (typeof window === "undefined") return false;
  if (!isStaleBuildError(err)) return false;

  const last = Number(window.sessionStorage.getItem(RELOAD_KEY) ?? "0");
  if (Number.isFinite(last) && Date.now() - last < RELOAD_COOLDOWN_MS) {
    return false; // 直前に試して駄目だった → 素直にエラー画面を見せる
  }
  window.sessionStorage.setItem(RELOAD_KEY, String(Date.now()));
  window.location.reload();
  return true;
}
