"use client";

import dynamic from "next/dynamic";
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
import { QuickActions } from "@/components/dashboard/quick-actions";
import { RecentActivity } from "@/components/dashboard/recent-activity";
import { SystemStatus } from "@/components/dashboard/system-status";
import { TopCampaigns } from "@/components/dashboard/top-campaigns";
import { Skeleton } from "@/components/ui";
import { useStats } from "@/lib/hooks";
import { formatUsd } from "@/lib/format";
import { cn } from "@/lib/utils";

const StatsTrendChart = dynamic(
  () => import("@/components/dashboard/stats-trend-chart").then((m) => m.StatsTrendChart),
  { ssr: false, loading: () => <Skeleton className="mb-6 h-56 w-full" /> },
);

function DashboardSkeleton({ count }: { count: number }) {
  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">
      <div className="flex flex-col gap-4 lg:col-span-2">
        <Skeleton className="h-64 w-full rounded-2xl" />
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          {Array.from({ length: count }).map((_, i) => (
            <div key={i} className="rounded-2xl border border-slate-200/80 bg-white p-5 shadow-card dark:border-slate-800 dark:bg-slate-900">
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
        // Two-column split from md (not lg) up — waiting for lg (1024px)
        // left this stacked as one long single column on plenty of real
        // laptop widths (a 1024px+ physical screen at >100% OS display
        // scaling reports a narrower CSS viewport than that), roughly
        // doubling the page's scroll height for no visual reason once it
        // has the room for two columns.
        <div className="grid grid-cols-1 gap-6 md:grid-cols-3 md:items-start">
          <div className="flex flex-col gap-6 md:col-span-2">
            <StatsTrendChart variant={variant} />
            <div
              className={cn(
                "grid grid-cols-1 gap-4 sm:grid-cols-2",
                cardCount === 5 ? "md:grid-cols-3 xl:grid-cols-5" : "md:grid-cols-4",
              )}
            >
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
                  tint="emerald"
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
                tint="purple"
              />
              <StatCard
                label="Spend Today"
                value={formatUsd(stats.data.usd_spend_today)}
                sublabel="LLM + audio cost"
                icon={DollarSign}
                tint="amber"
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
                tint="red"
              />
            </div>
            <RecentActivity variant={variant} />
          </div>
          <div className="flex flex-col gap-6">
            <QuickActions variant={variant} />
            {variant === "all" && (
              <>
                <SystemStatus />
                <TopCampaigns />
              </>
            )}
          </div>
        </div>
      )}
    </QueryBoundary>
  );
}
