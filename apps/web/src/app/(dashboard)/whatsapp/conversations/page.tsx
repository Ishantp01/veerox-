"use client";

import { PageHeader } from "@/components/layout/page-header";
import { ConversationsTable } from "@/components/conversations/conversations-table";

export default function WhatsAppConversationsPage() {
  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Conversations"
        description="WhatsApp sessions with your AI agent"
      />
      <ConversationsTable channel="whatsapp" detailBasePath="/whatsapp/conversations" />
    </div>
  );
}
