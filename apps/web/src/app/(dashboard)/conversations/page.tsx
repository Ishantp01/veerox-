"use client";

import { useState } from "react";
import { PageHeader } from "@/components/layout/page-header";
import { ConversationsTable } from "@/components/conversations/conversations-table";
import { Select } from "@/components/ui";

export default function ConversationsPage() {
  const [channel, setChannel] = useState<"voice" | "whatsapp" | "">("");

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Conversations"
        description="All conversations across AI Calling and AI WhatsApp"
        action={
          <Select
            value={channel}
            onChange={(v) => setChannel(v as "voice" | "whatsapp" | "")}
            aria-label="Filter conversations by channel"
          >
            <option value="">All channels</option>
            <option value="voice">Call conversations</option>
            <option value="whatsapp">WhatsApp conversations</option>
          </Select>
        }
      />
      <ConversationsTable channel={channel || undefined} detailBasePath="/conversations" />
    </div>
  );
}
