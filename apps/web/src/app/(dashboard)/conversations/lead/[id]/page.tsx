"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Users } from "lucide-react";

import { ChannelBadge } from "@/components/conversations/channel-badge";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  EmptyState,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
} from "@/components/ui";
import { formatDateTime, formatPhone } from "@/lib/format";
import { useLead } from "@/lib/hooks";

/** Conversation history of one lead — opened from the Leads table's
 * Conversation "View" button. Each row opens that conversation's transcript. */
export default function LeadConversationHistoryPage() {
  const router = useRouter();
  const params = useParams();
  const id = typeof params.id === "string" ? params.id : (params.id?.[0] ?? "");
  const lead = useLead(id);
  const conversations = lead.data?.conversations ?? [];

  return (
    <div className="mx-auto max-w-4xl">
      <Link
        href="/crm/leads"
        className="mb-4 inline-flex items-center gap-1.5 rounded-md text-sm text-slate-500 transition-colors hover:text-slate-800 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500"
      >
        <ArrowLeft size={15} aria-hidden />
        Leads
      </Link>
      <PageHeader
        title="Conversation History"
        description={
          lead.data
            ? `Conversations with ${lead.data.name ?? formatPhone(lead.data.phone)}`
            : "Conversations with this lead"
        }
      />
      <QueryBoundary
        isLoading={lead.isLoading}
        isError={lead.isError}
        error={lead.error}
        isEmpty={conversations.length === 0}
        onRetry={() => lead.refetch()}
        loadingFallback={
          <Table>
            <tbody>
              <SkeletonRows rows={4} cols={4} />
            </tbody>
          </Table>
        }
        emptyFallback={
          <EmptyState
            icon={Users}
            title="No conversations yet"
            description="This lead's conversations will appear here once they talk to the agent."
          />
        }
      >
        <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
          <Table>
            <thead>
              <TableRow isHeader>
                <TableHeader>Channel</TableHeader>
                <TableHeader>Started</TableHeader>
                <TableHeader>Ended</TableHeader>
                <TableHeader># Messages</TableHeader>
              </TableRow>
            </thead>
            <tbody>
              {conversations.map((c) => {
                const href = `/conversations/${c.id}`;
                return (
                  <TableRow
                    key={c.id}
                    role="link"
                    tabIndex={0}
                    aria-label={`Open conversation ${c.id}`}
                    onClick={() => router.push(href)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" || e.key === " ") {
                        e.preventDefault();
                        router.push(href);
                      }
                    }}
                    className="cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary-500"
                  >
                    <TableCell>
                      <ChannelBadge channel={c.channel === "voice" ? "voice" : "whatsapp"} />
                    </TableCell>
                    <TableCell className="text-xs text-slate-500">
                      {formatDateTime(c.started_at)}
                    </TableCell>
                    <TableCell className="text-xs text-slate-500">
                      {formatDateTime(c.ended_at)}
                    </TableCell>
                    <TableCell className="font-bold text-slate-800 dark:text-slate-100">
                      {c.message_count ?? "—"}
                    </TableCell>
                  </TableRow>
                );
              })}
            </tbody>
          </Table>
        </div>
      </QueryBoundary>
    </div>
  );
}
