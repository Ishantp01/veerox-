"use client";

import Link from "next/link";
import { ArrowRight, Megaphone, MessageCircle, Phone } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, EmptyState, Skeleton } from "@/components/ui";
import { useReportsCampaigns } from "@/lib/hooks";

const BAR_COLOR: Record<"voice" | "whatsapp", string> = {
  voice: "bg-blue-500",
  whatsapp: "bg-green-500",
};

const ICON_TINT: Record<"voice" | "whatsapp", string> = {
  voice: "bg-blue-500/20 text-blue-500",
  whatsapp: "bg-green-500/20 text-green-500",
};

/**
 * Best-converting campaigns ranked by qualification rate — reuses the same
 * GET /admin/reports/campaigns row the Reports table is built on, so the
 * numbers here always agree with the full report.
 */
export function TopCampaigns() {
  const { data, isLoading } = useReportsCampaigns();

  const top = [...(data ?? [])]
    .filter((c) => c.qualification_rate != null)
    .sort((a, b) => (b.qualification_rate ?? 0) - (a.qualification_rate ?? 0))
    .slice(0, 3);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Top Performing Campaigns</CardTitle>
        <Link
          href="/reports"
          className="flex items-center gap-1 text-xs font-semibold text-primary-600 hover:text-primary-700"
        >
          View all <ArrowRight size={12} aria-hidden />
        </Link>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="flex flex-col gap-4">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-10 w-full" />
            ))}
          </div>
        ) : top.length === 0 ? (
          <EmptyState
            icon={Megaphone}
            title="No campaigns yet"
            description="Qualification rates show up here once a campaign has completed calls or messages."
            className="border-0 bg-transparent py-8"
          />
        ) : (
          <div className="flex flex-col gap-4">
            {top.map((c) => {
              const pct = Math.round((c.qualification_rate ?? 0) * 100);
              const Icon = c.channel === "voice" ? Phone : MessageCircle;
              return (
                <div key={c.id}>
                  <div className="mb-2 flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <div className={`flex h-5 w-5 items-center justify-center rounded ${ICON_TINT[c.channel]}`}>
                        <Icon size={10} aria-hidden />
                      </div>
                      <span className="truncate text-[11px] font-medium text-slate-800 dark:text-slate-100">
                        {c.name}
                      </span>
                    </div>
                    <span className="text-[11px] font-bold text-slate-700 dark:text-slate-200">{pct}%</span>
                  </div>
                  <div className="h-1.5 w-full rounded-full bg-slate-100 dark:bg-slate-800">
                    <div
                      className={`h-1.5 rounded-full ${BAR_COLOR[c.channel]}`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export default TopCampaigns;
