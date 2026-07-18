"use client";

import Link from "next/link";
import { ArrowLeft, MessageSquare } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { EmptyState, Skeleton } from "@/components/ui";
import { TranscriptBubble } from "@/components/conversations/transcript-bubble";
import { LiveDot } from "@/components/conversations/live-dot";
import { useConversations, useConversationMessages } from "@/lib/hooks";
import { formatDuration, formatRelative } from "@/lib/format";

function TranscriptSkeleton() {
  return (
    <div className="flex max-w-2xl flex-col gap-4">
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className={i % 2 === 0 ? "flex flex-col items-start gap-1" : "flex flex-col items-end gap-1"}
        >
          <Skeleton className="h-3 w-24" />
          <Skeleton className="h-12 w-64 rounded-2xl" />
        </div>
      ))}
    </div>
  );
}

export interface ConversationDetailProps {
  id: string;
  /** Where the "back" link goes, e.g. "/whatsapp/conversations". */
  backHref: string;
  backLabel: string;
  /** Scopes the conversation-list lookup used to resolve isLive. */
  channel?: "voice" | "whatsapp";
}

/**
 * Transcript/detail view (UI plan §7). Shared by the unified conversation
 * detail route and the per-channel /whatsapp and /calling sections.
 */
export function ConversationDetail({ id, backHref, backLabel, channel }: ConversationDetailProps) {
  // The messages endpoint doesn't report whether the conversation has ended, so
  // we read ended_at from the (already cached + polled) conversation list. If
  // the row isn't found we default to live so we don't prematurely stop polling.
  const conversations = useConversations({ channel });
  const conversation = conversations.data?.find((c) => c.id === id);
  const isLive = conversation ? conversation.ended_at === null : true;

  const messages = useConversationMessages(id, { isLive });
  const rows = messages.data ?? [];

  return (
    <div className="mx-auto max-w-3xl">
      <Link
        href={backHref}
        className="mb-4 inline-flex items-center gap-1.5 rounded-md text-sm text-slate-500 transition-colors hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
      >
        <ArrowLeft size={15} aria-hidden />
        {backLabel}
      </Link>

      <PageHeader
        title="Transcript"
        description={`Conversation ${id}`}
        action={
          isLive && messages.dataUpdatedAt ? (
            <LiveDot label={`Live · updated ${formatRelative(new Date(messages.dataUpdatedAt).toISOString())}`} />
          ) : undefined
        }
      />

      {conversation?.recording_url && (
        <div className="mb-6 rounded-2xl border border-slate-200/80 bg-white p-4 shadow-card dark:border-slate-800 dark:bg-slate-900">
          <p className="mb-2 text-xs font-bold uppercase tracking-widest text-slate-400 dark:text-slate-500">
            Call Recording
            {conversation.recording_duration_secs != null &&
              ` · ${formatDuration(conversation.recording_duration_secs)}`}
          </p>
          <audio controls src={conversation.recording_url} className="w-full">
            Your browser does not support the audio element.
          </audio>
        </div>
      )}

      <QueryBoundary
        isLoading={messages.isLoading}
        isError={messages.isError}
        error={messages.error}
        isEmpty={!messages.isLoading && rows.length === 0}
        onRetry={() => messages.refetch()}
        loadingFallback={<TranscriptSkeleton />}
        emptyFallback={
          <EmptyState
            icon={MessageSquare}
            title="No messages yet"
            description="This conversation has no transcript to show. Messages appear here as the agent and user talk."
          />
        }
      >
        <div className="flex max-w-2xl flex-col gap-4">
          {rows.map((msg) => (
            <TranscriptBubble
              key={msg.id}
              role={msg.role}
              content={msg.content}
              timestamp={msg.created_at}
              isVoice={msg.channel === "voice" || (msg.audio_secs ?? 0) > 0}
            />
          ))}
        </div>
      </QueryBoundary>
    </div>
  );
}
