"use client";

import { DollarSign, TrendingUp, Users, BadgeCheck } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { StatCard } from "@/components/dashboard/stat-card";
import { ChannelBadge } from "@/components/conversations/channel-badge";
import {
  LEAD_QUALIFICATION_LABELS,
} from "@/components/leads/qualification-badge";
import { LEAD_STATUS_LABELS } from "@/components/leads/status-badge";
import { Card, CardContent, CardHeader, CardTitle, Skeleton, Table, TableCell, TableHeader, TableRow } from "@/components/ui";
import { usePipeline, useRevenueSummary } from "@/lib/hooks";
import { formatUsd } from "@/lib/format";

export default function SalesDashboardPage() {
  const pipeline = usePipeline();
  const revenue = useRevenueSummary();

  const totalLeads = pipeline.data?.stages.reduce((sum, s) => sum + s.count, 0) ?? 0;
  const qualifiedCount =
    revenue.data?.qualification_funnel.find((s) => s.qualification_status === "qualified")?.count ?? 0;

  const isError = pipeline.isError || revenue.isError;

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Sales Dashboard"
        description="Pipeline value and conversion, aggregated across both AI Calling and AI WhatsApp."
      />

      {!isError && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Pipeline value"
            value={formatUsd(revenue.data?.total_pipeline_value)}
            icon={DollarSign}
            tint="primary"
          />
          <StatCard
            label="Won value"
            value={formatUsd(revenue.data?.won_value)}
            icon={TrendingUp}
            tint="emerald"
          />
          <StatCard label="Total leads" value={totalLeads} icon={Users} tint="purple" />
          <StatCard
            label="Qualified leads"
            value={qualifiedCount}
            icon={BadgeCheck}
            tint="amber"
            sublabel="By review stage, not pipeline status"
          />
        </div>
      )}

      {isError ? (
        <div className="mt-8">
          <QueryBoundary
            isLoading={false}
            isError
            onRetry={() => {
              pipeline.refetch();
              revenue.refetch();
            }}
          >
            <></>
          </QueryBoundary>
        </div>
      ) : (
        <>
          <div className="mt-8 grid grid-cols-1 gap-6 lg:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle>Pipeline by stage</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <QueryBoundary
                  isLoading={pipeline.isLoading}
                  isError={false}
                  loadingFallback={<div className="p-4"><Skeleton className="h-40 w-full" /></div>}
                >
                  <div className="overflow-x-auto">
                    <Table>
                      <thead>
                        <TableRow isHeader>
                          <TableHeader>Stage</TableHeader>
                          <TableHeader>Leads</TableHeader>
                          <TableHeader>Value</TableHeader>
                        </TableRow>
                      </thead>
                      <tbody>
                        {(pipeline.data?.stages ?? []).map((stage) => (
                          <TableRow key={stage.status}>
                            <TableCell className="font-semibold text-slate-800 dark:text-slate-100">
                              {LEAD_STATUS_LABELS[stage.status]}
                            </TableCell>
                            <TableCell className="tabular-nums">{stage.count}</TableCell>
                            <TableCell className="tabular-nums">{formatUsd(stage.value)}</TableCell>
                          </TableRow>
                        ))}
                      </tbody>
                    </Table>
                  </div>
                </QueryBoundary>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Review Stage funnel</CardTitle>
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                  Rep-driven review, separate from the pipeline stages on the left.
                </p>
              </CardHeader>
              <CardContent className="p-0">
                <QueryBoundary
                  isLoading={revenue.isLoading}
                  isError={false}
                  loadingFallback={<div className="p-4"><Skeleton className="h-40 w-full" /></div>}
                >
                  <div className="overflow-x-auto">
                    <Table>
                      <thead>
                        <TableRow isHeader>
                          <TableHeader>Stage</TableHeader>
                          <TableHeader>Leads</TableHeader>
                        </TableRow>
                      </thead>
                      <tbody>
                        {(revenue.data?.qualification_funnel ?? []).map((stage) => (
                          <TableRow key={stage.qualification_status}>
                            <TableCell className="font-semibold text-slate-800 dark:text-slate-100">
                              {LEAD_QUALIFICATION_LABELS[stage.qualification_status]}
                            </TableCell>
                            <TableCell className="tabular-nums">{stage.count}</TableCell>
                          </TableRow>
                        ))}
                      </tbody>
                    </Table>
                  </div>
                </QueryBoundary>
              </CardContent>
            </Card>
          </div>

          <Card className="mt-6">
            <CardHeader>
              <CardTitle>By channel</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <QueryBoundary
                isLoading={revenue.isLoading}
                isError={false}
                loadingFallback={<div className="p-4"><Skeleton className="h-24 w-full" /></div>}
              >
                <div className="overflow-x-auto">
                  <Table>
                    <thead>
                      <TableRow isHeader>
                        <TableHeader>Channel</TableHeader>
                        <TableHeader>Leads</TableHeader>
                        <TableHeader>Value</TableHeader>
                      </TableRow>
                    </thead>
                    <tbody>
                      {(revenue.data?.by_channel ?? []).map((row) => (
                        <TableRow key={row.channel}>
                          <TableCell>
                            {row.channel === "voice" || row.channel === "whatsapp" ? (
                              <ChannelBadge channel={row.channel} />
                            ) : (
                              row.channel
                            )}
                          </TableCell>
                          <TableCell className="tabular-nums">{row.count}</TableCell>
                          <TableCell className="tabular-nums">{formatUsd(row.value)}</TableCell>
                        </TableRow>
                      ))}
                    </tbody>
                  </Table>
                </div>
              </QueryBoundary>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
