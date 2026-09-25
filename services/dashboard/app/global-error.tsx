"use client";

import ErrorScreen from "@/components/ErrorScreen";
import "./globals.css";

// 最後の砦。ルートレイアウト自体が壊れたときはここだけが描画されるので、
// ⚠html/bodyを自前で持つ必要がある (レイアウトが差し変わるため)。
export default function GlobalError(props: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="ja">
      <body>
        <main>
          <ErrorScreen {...props} />
        </main>
      </body>
    </html>
  );
}
