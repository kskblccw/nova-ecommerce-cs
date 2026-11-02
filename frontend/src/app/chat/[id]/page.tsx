import { ChatWorkspace } from "@/components/chat-workspace";

export default async function ChatDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <ChatWorkspace initialId={id} />;
}
