"use client";

import { PageHeader } from "@/components/layout/page-header";
import { ConversationsTable } from "@/components/conversations/conversations-table";

export default function CallingConversationsPage() {
  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Conversations"
        description="Voice call sessions with your AI agent"
      />
      <ConversationsTable channel="voice" detailBasePath="/calling/conversations" />
    </div>
  );
}
