"use client";

import {
  AlertTriangle,
  DollarSign,
  MessageSquare,
  PhoneCall,
  Sparkles,
  Users,
} from "lucide-react";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { StatCard } from "@/components/dashboard/stat-card";
import { StatsTrendChart } from "@/components/dashboard/stats-trend-chart";
import { QuickActions } from "@/components/dashboard/quick-actions";
import { RecentActivity } from "@/components/dashboard/recent-activity";
import { Skeleton } from "@/components/ui";
import { useStats } from "@/lib/hooks";
import { formatUsd } from "@/lib/format";

function DashboardSkeleton({ count }: { count: number }) {
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
      <div className="flex flex-col gap-4 lg:col-span-2">
        <Skeleton className="h-64 w-full rounded-2xl" />
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {Array.from({ length: count }).map((_, i) => (
            <div key={i} className="rounded-2xl border border-slate-200 bg-white p-6 shadow-elevated">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="mt-4 h-9 w-16" />
              <Skeleton className="mt-3 h-3 w-24" />
            </div>
          ))}
        </div>
      </div>
      <Skeleton className="h-80 w-full rounded-2xl" />
    </div>
  );
}

export interface StatsGridProps {
  /** "all" = the original 5-card unified view; "whatsapp"/"voice" show the
   * subset of /admin/stats relevant to that channel's section dashboard. */
  variant: "all" | "whatsapp" | "voice";
}

/**
 * Dashboard body backed by GET /admin/stats: a trend chart + stat cards on
 * the left, quick-action shortcuts on the right so the page reads as a
 * working tool rather than a bare metrics readout. Shared by the root
 * landing page ("all") and the per-channel /whatsapp and /calling dashboards.
 */
export function StatsGrid({ variant }: StatsGridProps) {
  const stats = useStats();
  const cardCount = variant === "all" ? 5 : 4;

  return (
    <QueryBoundary
      isLoading={stats.isLoading}
      isError={stats.isError}
      error={stats.error}
      onRetry={() => stats.refetch()}
      loadingFallback={<DashboardSkeleton count={cardCount} />}
    >
      {stats.data && (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-3 lg:items-start">
          <div className="flex flex-col gap-6 lg:col-span-2">
            <StatsTrendChart variant={variant} />
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {variant === "all" && (
                <StatCard
                  label="Users Today"
                  value={stats.data.users_today}
                  icon={Users}
                  tint="primary"
                />
              )}
              {(variant === "all" || variant === "voice") && (
                <StatCard
                  label="Calls Today"
                  value={stats.data.calls_today}
                  icon={PhoneCall}
                  tint="sky"
                />
              )}
              {variant === "whatsapp" && (
                <StatCard
                  label="Messages Today"
                  value={stats.data.whatsapp_messages_today ?? 0}
                  icon={MessageSquare}
                  tint="emerald"
                />
              )}
              <StatCard
                label="Leads Today"
                value={
                  variant === "whatsapp"
                    ? stats.data.leads_today_whatsapp ?? 0
                    : variant === "voice"
                      ? stats.data.leads_today_voice ?? 0
                      : stats.data.leads_today
                }
                icon={Sparkles}
                tint="emerald"
              />
              <StatCard
                label="Spend Today"
                value={formatUsd(stats.data.usd_spend_today)}
                sublabel="LLM + audio cost"
                icon={DollarSign}
                tint="rose"
              />
              <StatCard
                label="Errors Today"
                value={stats.data.error_count_today ?? 0}
                sublabel={
                  stats.data.p50_turn_latency_ms != null
                    ? `p50 latency ${stats.data.p50_turn_latency_ms} ms`
                    : "p50 latency —"
                }
                icon={AlertTriangle}
                tint="amber"
              />
            </div>
            <RecentActivity variant={variant} />
          </div>
          <QuickActions variant={variant} />
        </div>
      )}
    </QueryBoundary>
  );
}
