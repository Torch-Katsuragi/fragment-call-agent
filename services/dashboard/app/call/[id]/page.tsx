import CallView from "@/components/CallView";

export const dynamic = "force-dynamic";

// 通話詳細: ライブ/終了済みの切り替え・データ取得はすべて CallView (client) 側で行う。
// 文字起こしはDBポーリングが本体なので、通話中でも過去でも同じ経路で確実に表示される。
// ?op=1 = 受話ボタン経由 → 最初からoperator (マイクON) で入室する
export default async function CallPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ op?: string }>;
}) {
  const { id } = await params;
  const { op } = await searchParams;
  return <CallView id={id} initialOperator={op === "1"} />;
}
