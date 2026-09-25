"use client";

import ErrorScreen from "@/components/ErrorScreen";

// ページ描画中のエラーを受け止める。サイドバーやレイアウトは生き残るので、
// ここから他の画面へ移動して復帰できる。
export default function Error(props: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return <ErrorScreen {...props} />;
}
