"use client";

import Link from "next/link";
import { ArrowRight, MessageSquare } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, EmptyState, Skeleton } from "@/components/ui";
import { ChannelBadge } from "@/components/conversations/channel-badge";
import { LiveDot } from "@/components/conversations/live-dot";
import { useConversations } from "@/lib/hooks";
import { formatRelative } from "@/lib/format";

export interface RecentActivityProps {
  variant: "all" | "whatsapp" | "voice";
}

const DETAIL_BASE: Record<"voice" | "whatsapp", string> = {
  voice: "/calling/conversations",
  whatsapp: "/whatsapp/conversations",
};

/** Latest few conversations, newest first — gives the dashboard a live
 * "things are happening" feed instead of ending at a bare stat-card row. */
export function RecentActivity({ variant }: RecentActivityProps) {
  const channel = variant === "all" ? undefined : variant;
  const { data, isLoading } = useConversations({ channel, limit: 6 });
  const rows = data ?? [];
  const viewAllHref = variant === "whatsapp" ? "/whatsapp/conversations" : "/calling/conversations";

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Recent conversations</CardTitle>
        <Link
          href={viewAllHref}
          className="flex items-center gap-1 text-xs font-semibold text-primary-600 hover:text-primary-700"
        >
          View all <ArrowRight size={12} aria-hidden />
        </Link>
      </CardHeader>
      <CardContent className="p-3">
        {isLoading ? (
          <div className="flex flex-col gap-2 p-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : rows.length === 0 ? (
          <EmptyState
            icon={MessageSquare}
            title="No conversations yet"
            description="They'll show up here as soon as the agent talks to someone."
            className="border-0 bg-transparent"
          />
        ) : (
          <ul className="flex flex-col">
            {rows.map((c) => {
              const href = `${DETAIL_BASE[c.channel]}/${c.id}`;
              const isLive = c.ended_at === null;
              return (
                <li key={c.id}>
                  <Link
                    href={href}
                    className="flex items-center gap-3 rounded-xl px-3 py-2.5 transition-colors hover:bg-primary-50/60 dark:hover:bg-primary-500/10"
                  >
                    <ChannelBadge channel={c.channel} />
                    <span className="min-w-0 flex-1 truncate text-sm text-slate-600 dark:text-slate-300">
                      {c.message_count ?? 0} message{c.message_count === 1 ? "" : "s"}
                    </span>
                    {isLive ? (
                      <LiveDot />
                    ) : (
                      <span className="shrink-0 text-xs text-slate-400 dark:text-slate-500">
                        {formatRelative(c.started_at)}
                      </span>
                    )}
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
