"use client";

import { useState } from "react";
import { CheckCircle2, Search } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { HumanSupportTable } from "@/components/human-support/human-support-table";
import { EmptyState, Input, Pagination, Select, SkeletonRows, Table } from "@/components/ui";
import { useClientPagination, useHumanSupport } from "@/lib/hooks";
import type { HumanSupport, HandoffQueueEntry, Lead } from "@/lib/types";

/**
 * Map a persisted Lead row (intent='human_support') into the unified row shape.
 * reason/urgency live in metadata_ — see apps/api/core/tools.py:transfer_to_human.
 */
export function leadToHumanSupport(lead: Lead): HumanSupport {
  const meta = lead.metadata_ ?? {};
  return {
    source: "lead",
    id: lead.id,
    created_at: lead.created_at,
    user_id: lead.user_id,
    user_phone: lead.phone,
    reason: typeof meta.reason === "string" ? meta.reason : "—",
    urgency: typeof meta.urgency === "string" ? meta.urgency : "medium",
    channel: lead.channel,
    conversation_id: lead.conversation_id,
    claimed_by_account_user_id: lead.claimed_by_account_user_id,
    claimed_by_name: lead.claimed_by_name,
    claimed_at: lead.claimed_at,
    tags: lead.tags,
  };
}

/** Map a live Redis-queue entry into the unified row shape. */
export function queueEntryToHumanSupport(entry: HandoffQueueEntry): HumanSupport {
  return {
    source: "queue",
    created_at: entry.requested_at,
    user_id: entry.user_id,
    user_phone: entry.phone ?? null,
    reason: entry.reason,
    urgency: entry.urgency ?? "medium",
    channel: entry.channel,
    conversation_id: entry.conversation_id ?? null,
  };
}

export interface HumanSupportViewProps {
  title: string;
  description: string;
  /** Scopes both the Lead-backed and live-queue rows to a single channel. */
  channel?: "voice" | "whatsapp";
  /** Base path for the conversation link, e.g. "/whatsapp/conversations". */
  conversationBasePath: string;
}

/**
 * HumanSupport feed (UI plan §7.2). Shared by the unified HumanSupport page
 * and the per-channel /whatsapp/human-support and /calling/human-support pages.
 */
export function HumanSupportView({
  title,
  description,
  channel,
  conversationBasePath,
}: HumanSupportViewProps) {
  // Only the unified (cross-channel) view lets the user pick a channel —
  // per-channel pages already have `channel` fixed by their caller.
  const [channelFilter, setChannelFilter] = useState<"voice" | "whatsapp" | "">("");
  const effectiveChannel = channel ?? (channelFilter || undefined);

  const { data, isLoading, isError, error, refetch } = useHumanSupport({ channel: effectiveChannel });
  const [search, setSearch] = useState("");

  // Flatten: queue entries first (live, pending pickup), then persisted leads
  // (history). A queue entry becomes a lead only after it's handled, which
  // removes it from the queue — so no de-dup is needed today.
  const allHumanSupport: HumanSupport[] = [
    ...(data?.queue ?? []).map(queueEntryToHumanSupport),
    ...(data?.recent_leads ?? []).map(leadToHumanSupport),
  ];
  const term = search.trim().toLowerCase();
  const humanSupport = term
    ? allHumanSupport.filter((e) =>
        [e.user_phone, e.reason, ...(e.tags ?? [])]
          .filter(Boolean)
          .some((f) => f!.toLowerCase().includes(term)),
      )
    : allHumanSupport;
  const pager = useClientPagination(humanSupport, 20, `${effectiveChannel}|${search}`);

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title={title}
        description={description}
        action={
          <div className="flex flex-wrap items-center gap-3">
            <div className="relative">
              <Search
                size={14}
                aria-hidden
                className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
              />
              <Input
                id="human-support-search"
                type="search"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search number, reason, or tag…"
                aria-label="Search Human Support requests by number, reason, or tag"
                className="w-56 pl-8"
              />
            </div>
            {channel === undefined && (
              <Select
                value={channelFilter}
                onChange={(v) => setChannelFilter(v as "voice" | "whatsapp" | "")}
                aria-label="Filter Human Support requests by channel"
              >
                <option value="">All channels</option>
                <option value="voice">Call requests</option>
                <option value="whatsapp">WhatsApp requests</option>
              </Select>
            )}
          </div>
        }
      />

      <QueryBoundary
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={humanSupport.length === 0}
        onRetry={() => refetch()}
        loadingFallback={
          <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
            <Table>
              <tbody>
                <SkeletonRows rows={4} cols={8} />
              </tbody>
            </Table>
          </div>
        }
        emptyFallback={
          <EmptyState
            icon={CheckCircle2}
            title="No pending Human Support requests"
            description="All clear — no human handoffs needed right now."
          />
        }
      >
        <HumanSupportTable humanSupport={pager.pageRows} conversationBasePath={conversationBasePath} />
        <Pagination
          page={pager.page}
          pageSize={pager.pageSize}
          rowCount={pager.rowCount}
          hasNextPage={pager.hasNextPage}
          onPrev={pager.onPrev}
          onNext={pager.onNext}
        />
      </QueryBoundary>
    </div>
  );
}
