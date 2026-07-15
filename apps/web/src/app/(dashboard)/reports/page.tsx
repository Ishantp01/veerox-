"use client";

import { useMemo, useState } from "react";
import { BarChart3, Download, Megaphone } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { ReportsTrendChart } from "@/components/reports/trend-chart";
import { CampaignsConversionTable } from "@/components/reports/campaigns-conversion-table";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  Skeleton,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  useToast,
} from "@/components/ui";
import { downloadCsv } from "@/lib/download-csv";
import { useReportsCampaigns, useReportsTimeseries } from "@/lib/hooks";
import { formatShortDate, formatUsd } from "@/lib/format";

const DATE_RANGES = [
  { label: "Last 7 days", days: 7 },
  { label: "Last 30 days", days: 30 },
  { label: "Last 90 days", days: 90 },
] as const;

function SummaryStat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 dark:border-slate-800 dark:bg-slate-900">
      <p className="text-[11px] font-bold uppercase tracking-widest text-slate-400 dark:text-slate-500">
        {label}
      </p>
      <p className="mt-1.5 text-2xl font-extrabold tabular-nums tracking-tight text-slate-900 dark:text-slate-50">
        {value}
      </p>
    </div>
  );
}

export default function ReportsPage() {
  const [days, setDays] = useState<number>(30);
  const [exporting, setExporting] = useState(false);
  const { toast } = useToast();

  const timeseries = useReportsTimeseries(days);
  const campaigns = useReportsCampaigns();
  const campaignRows = campaigns.data ?? [];

  const points = useMemo(() => timeseries.data ?? [], [timeseries.data]);
  const totals = useMemo(
    () =>
      points.reduce(
        (acc, p) => ({
          calls: acc.calls + p.calls,
          whatsapp: acc.whatsapp + p.whatsapp_messages,
          qualified: acc.qualified + p.qualified_count,
          spend: acc.spend + p.usd_spend,
        }),
        { calls: 0, whatsapp: 0, qualified: 0, spend: 0 }
      ),
    [points]
  );

  async function handleExportQualified() {
    setExporting(true);
    try {
      const stamp = new Date().toISOString().slice(0, 10);
      await downloadCsv("/admin/leads.csv?status=qualified", `qualified-leads-${stamp}.csv`);
      toast({ title: "Export started", description: "Your CSV download is ready.", variant: "success" });
    } catch (err: unknown) {
      toast({
        title: "Export failed",
        description: err instanceof Error ? err.message : "Could not export leads.",
        variant: "error",
      });
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Reports"
        description="Trends across both channels and per-campaign qualification rates, for the sales team."
        action={
          <Button variant="outline" size="sm" onClick={handleExportQualified} loading={exporting}>
            {!exporting && <Download size={14} aria-hidden />}
            Export qualified leads
          </Button>
        }
      />

      {/* Date range — one row above everything it scopes, per the dataviz
          filter convention: every chart/stat/table below re-renders against
          the same slice, so the numbers always agree. */}
      <div className="mb-6 flex gap-1.5">
        {DATE_RANGES.map((range) => (
          <button
            key={range.days}
            type="button"
            onClick={() => setDays(range.days)}
            className={`rounded-lg px-3 py-1.5 text-xs font-semibold transition-colors ${
              days === range.days
                ? "bg-primary-600 text-white shadow-sm"
                : "bg-slate-100 text-slate-600 hover:bg-slate-200 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
            }`}
          >
            {range.label}
          </button>
        ))}
      </div>

      <QueryBoundary
        isLoading={timeseries.isLoading}
        isError={timeseries.isError}
        error={timeseries.error}
        onRetry={() => timeseries.refetch()}
        loadingFallback={
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-20 rounded-xl" />
            ))}
          </div>
        }
      >
        <div className="mb-6 grid grid-cols-2 gap-3 sm:grid-cols-4">
          <SummaryStat label="Calls" value={totals.calls} />
          <SummaryStat label="WhatsApp Messages" value={totals.whatsapp} />
          <SummaryStat label="Qualified Leads" value={totals.qualified} />
          <SummaryStat label="Spend" value={formatUsd(totals.spend)} />
        </div>

        <Card className="mb-8">
          <CardHeader>
            <CardTitle>Daily trend</CardTitle>
          </CardHeader>
          <CardContent>
            {points.length === 0 ? (
              <EmptyState
                icon={BarChart3}
                title="No activity in this window"
                description="Calls, WhatsApp messages, and qualified leads will chart here once outreach starts."
              />
            ) : (
              <>
                <ReportsTrendChart data={points} />
                {/* Table view of the same data — the accessible fallback the
                    dataviz skill requires alongside any chart, and useful on
                    its own for a sales-ops audit trail. */}
                <div className="mt-4 max-h-56 overflow-y-auto rounded-xl border border-slate-100 dark:border-slate-800">
                  <Table>
                    <thead className="sticky top-0 bg-white dark:bg-slate-900">
                      <TableRow isHeader>
                        <TableHeader>Date</TableHeader>
                        <TableHeader>Calls</TableHeader>
                        <TableHeader>WhatsApp</TableHeader>
                        <TableHeader>Qualified</TableHeader>
                        <TableHeader>Spend</TableHeader>
                      </TableRow>
                    </thead>
                    <tbody>
                      {[...points].reverse().map((p) => (
                        <TableRow key={p.date}>
                          <TableCell className="tabular-nums">{formatShortDate(p.date)}</TableCell>
                          <TableCell className="tabular-nums">{p.calls}</TableCell>
                          <TableCell className="tabular-nums">{p.whatsapp_messages}</TableCell>
                          <TableCell className="tabular-nums">{p.qualified_count}</TableCell>
                          <TableCell className="tabular-nums">{formatUsd(p.usd_spend)}</TableCell>
                        </TableRow>
                      ))}
                    </tbody>
                  </Table>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </QueryBoundary>

      <h2 className="mb-3 text-sm font-bold text-slate-700 dark:text-slate-200">
        Campaign conversion
      </h2>
      <QueryBoundary
        isLoading={campaigns.isLoading}
        isError={campaigns.isError}
        error={campaigns.error}
        isEmpty={campaignRows.length === 0}
        onRetry={() => campaigns.refetch()}
        loadingFallback={
          <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm">
            <Table>
              <tbody>
                <SkeletonRows rows={3} cols={6} />
              </tbody>
            </Table>
          </div>
        }
        emptyFallback={
          <EmptyState
            icon={Megaphone}
            title="No campaigns yet"
            description="Once a voice or WhatsApp campaign runs, its qualification rate shows up here."
          />
        }
      >
        <CampaignsConversionTable rows={campaignRows} />
      </QueryBoundary>
    </div>
  );
}
