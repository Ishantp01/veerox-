"use client";

import { useState } from "react";
import { PageHeader } from "@/components/layout/page-header";
import { ConversationsTable } from "@/components/conversations/conversations-table";
import { Select } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";

export default function ConversationsPage() {
  const [channel, setChannel] = useState<"voice" | "whatsapp" | "">("");
  const { user } = useAuth();
  const scopedToMember = user?.role === "member" && !user?.is_superuser;

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Conversations"
        description={
          scopedToMember
            ? "Conversations for leads assigned to you, across AI Calling and AI WhatsApp"
            : "All conversations across AI Calling and AI WhatsApp"
        }
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
