import { Suspense } from "react";

import { ChatWorkspace } from "@/components/chat/chat-workspace";

export default function ChatPage() {
  return (
    <Suspense fallback={null}>
      <ChatWorkspace />
    </Suspense>
  );
}
