"use client";

import { useParams } from "next/navigation";
import { ConversationDetail } from "@/components/conversations/conversation-detail";

export default function ConversationTranscriptPage() {
  const params = useParams();
  const id = typeof params.id === "string" ? params.id : (params.id?.[0] ?? "");

  return <ConversationDetail id={id} backHref="/conversations" backLabel="Conversations" />;
}
