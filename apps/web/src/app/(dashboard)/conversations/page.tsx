"use client";

import { useState } from "react";
import { PageHeader } from "@/components/layout/page-header";
import { ConversationsTable } from "@/components/conversations/conversations-table";

export default function ConversationsPage() {
  const [channel, setChannel] = useState<"voice" | "whatsapp" | "">("");

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Conversations"
        description="All conversations across AI Calling and AI WhatsApp"
        action={
          <select
            value={channel}
            onChange={(e) => setChannel(e.target.value as "voice" | "whatsapp" | "")}
            aria-label="Filter conversations by channel"
            className="rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
          >
            <option value="">All channels</option>
            <option value="voice">Call conversations</option>
            <option value="whatsapp">WhatsApp conversations</option>
          </select>
        }
      />
      <ConversationsTable channel={channel || undefined} detailBasePath="/conversations" />
    </div>
  );
}
