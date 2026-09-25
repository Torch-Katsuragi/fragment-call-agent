import RoomView from "@/components/RoomView";

// Next.js 15: params は Promise
export default async function RoomPage({ params }: { params: Promise<{ room: string }> }) {
  const { room } = await params;
  return <RoomView room={decodeURIComponent(room)} />;
}
