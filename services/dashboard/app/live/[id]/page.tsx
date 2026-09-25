import LiveView from "@/components/LiveView";

export const dynamic = "force-dynamic";

// スマホのロック画面の上に出す軽量表示 (2026-09-19)。?mode=visualizer | chat。
// 操作は何も無い。AI が応対している通話を「見るだけ」。詳細は components/LiveView.tsx
export default async function Page({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ mode?: string; debug?: string }>;
}) {
  const { id } = await params;
  const { mode, debug } = await searchParams;
  return <LiveView id={id} mode={mode === "chat" ? "chat" : "visualizer"} debug={debug === "1"} />;
}
