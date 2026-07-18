"use client";

import { useRouter } from "next/navigation";
import { Inbox } from "lucide-react";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Badge,
  EmptyState,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
} from "@/components/ui";
import { ChannelBadge } from "@/components/conversations/channel-badge";
import { LiveDot } from "@/components/conversations/live-dot";
import { useConversations } from "@/lib/hooks";
import { formatDateTime } from "@/lib/format";

export interface ConversationsTableProps {
  /** When set, the list is server-filtered to this channel and the Channel
   * column is omitted (every row is already known to be that channel). */
  channel?: "voice" | "whatsapp";
  /** Base path for row navigation, e.g. "/whatsapp/conversations". */
  detailBasePath: string;
}

/**
 * Conversation list table (UI plan §7). Shared by the unified /conversations
 * view and the per-channel /whatsapp and /calling sections — pass `channel`
 * to scope the query and drop the redundant Channel column.
 */
export function ConversationsTable({ channel, detailBasePath }: ConversationsTableProps) {
  const router = useRouter();
  const conversations = useConversations({ channel });

  const rows = conversations.data ?? [];
  const columns = channel
    ? (["Live", "Started", "Ended", "# Messages"] as const)
    : (["Live", "Channel", "Started", "Ended", "# Messages"] as const);

  return (
    <div className="overflow-hidden rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
      <Table>
        <thead>
          <TableRow isHeader>
            {columns.map((col) => (
              <TableHeader key={col}>{col}</TableHeader>
            ))}
          </TableRow>
        </thead>
        <tbody>
          <QueryBoundaryRows
            columnCount={columns.length}
            isLoading={conversations.isLoading}
            isError={conversations.isError}
            error={conversations.error}
            isEmpty={!conversations.isLoading && rows.length === 0}
            onRetry={() => conversations.refetch()}
          >
            {rows.map((c) => {
              const isLive = c.ended_at === null;
              return (
                <TableRow
                  key={c.id}
                  role="link"
                  tabIndex={0}
                  aria-label={`Open conversation ${c.id}`}
                  onClick={() => router.push(`${detailBasePath}/${c.id}`)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      router.push(`${detailBasePath}/${c.id}`);
                    }
                  }}
                  className="cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary-500"
                >
                  <TableCell>
                    {isLive ? <LiveDot /> : <Badge variant="ended">Ended</Badge>}
                  </TableCell>
                  {!channel && (
                    <TableCell>
                      <ChannelBadge channel={c.channel} />
                    </TableCell>
                  )}
                  <TableCell className="text-xs text-slate-500">
                    {formatDateTime(c.started_at)}
                  </TableCell>
                  <TableCell className="text-xs text-slate-500">
                    {formatDateTime(c.ended_at)}
                  </TableCell>
                  <TableCell className="font-bold text-slate-800">
                    {c.message_count ?? "—"}
                  </TableCell>
                </TableRow>
              );
            })}
          </QueryBoundaryRows>
        </tbody>
      </Table>
    </div>
  );
}

/**
 * Wraps QueryBoundary's loading/empty/error states in a full-width table row so
 * they render correctly inside `<tbody>`. Loading shows skeleton rows; empty and
 * error span all columns.
 */
function QueryBoundaryRows({
  columnCount,
  isLoading,
  isError,
  error,
  isEmpty,
  onRetry,
  children,
}: {
  columnCount: number;
  isLoading: boolean;
  isError: boolean;
  error: Error | null;
  isEmpty: boolean;
  onRetry: () => void;
  children: React.ReactNode;
}) {
  if (isLoading) {
    return <SkeletonRows rows={6} cols={columnCount} />;
  }

  if (isError || isEmpty) {
    return (
      <tr>
        <td colSpan={columnCount} className="p-0">
          <QueryBoundary
            isLoading={false}
            isError={isError}
            error={error}
            isEmpty={isEmpty}
            onRetry={onRetry}
            emptyFallback={
              <EmptyState
                icon={Inbox}
                title="No conversations yet"
                description="Conversations appear here once the agent starts talking to a user."
                className="border-0"
              />
            }
          >
            {null}
          </QueryBoundary>
        </td>
      </tr>
    );
  }

  return <>{children}</>;
}
