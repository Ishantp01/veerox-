"use client";

import Link from "next/link";
import { ArrowRight, Megaphone, MessageSquareText, UserCheck } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, EmptyState, Skeleton } from "@/components/ui";
import { StatCard } from "@/components/dashboard/stat-card";
import { CampaignStatusBadge } from "@/components/campaigns/campaign-status-badge";
import { useCampaigns, useStats } from "@/lib/hooks";

/**
 * Right-rail panel for the Send WhatsApp Message page — mirrors the
 * mockup's "Message Analytics" + "Recent Campaigns" layout, backed by
 * existing endpoints (GET /admin/stats, GET /admin/campaigns?channel=whatsapp)
 * rather than new ones.
 */
export function WhatsAppSendAnalyticsPanel() {
  const stats = useStats();
  const campaigns = useCampaigns("whatsapp");
  const recentCampaigns = (campaigns.data ?? []).slice(0, 5);

  return (
    <div className="flex w-full max-w-sm flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle>Message Analytics</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-3">
          {stats.isLoading ? (
            <>
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-24 w-full" />
            </>
          ) : (
            <>
              <StatCard
                label="Sent today"
                value={stats.data?.whatsapp_messages_today ?? 0}
                icon={MessageSquareText}
                tint="emerald"
              />
              <StatCard
                label="Leads today"
                value={stats.data?.leads_today_whatsapp ?? 0}
                icon={UserCheck}
                tint="purple"
              />
            </>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle>Recent Campaigns</CardTitle>
          <Link
            href="/automation/campaigns"
            className="flex items-center gap-1 text-xs font-semibold text-primary-600 hover:text-primary-700"
          >
            View all <ArrowRight size={12} aria-hidden />
          </Link>
        </CardHeader>
        <CardContent className="p-3">
          {campaigns.isLoading ? (
            <div className="flex flex-col gap-2 p-3">
              <Skeleton className="h-12 w-full" />
              <Skeleton className="h-12 w-full" />
            </div>
          ) : recentCampaigns.length === 0 ? (
            <EmptyState
              icon={Megaphone}
              title="No WhatsApp campaigns yet"
              description="Bulk-message a lead list from Automation → Campaigns."
              className="border-0 bg-transparent"
            />
          ) : (
            <ul className="flex flex-col">
              {recentCampaigns.map((c) => (
                <li key={c.id}>
                  <Link
                    href={`/automation/campaigns/${c.id}`}
                    className="flex items-center gap-3 rounded-xl px-3 py-2.5 transition-colors hover:bg-primary-50/60 dark:hover:bg-primary-500/10"
                  >
                    <span className="min-w-0 flex-1 truncate text-sm text-slate-700 dark:text-slate-200">
                      {c.name}
                    </span>
                    <CampaignStatusBadge status={c.status} />
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
