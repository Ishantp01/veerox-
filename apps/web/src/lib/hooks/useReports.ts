import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { ReportsCampaignRow, ReportsTimeseriesPoint } from "@/lib/types";

/**
 * Daily trend buckets for the reports page. Historical data — unlike the
 * live dashboard stats, there's no reason to poll; the 30s default
 * staleTime (see lib/query.ts) is enough, callers can refetch() on demand.
 *
 * GET /admin/reports/timeseries?days= → ReportsTimeseriesPoint[]
 */
export function useReportsTimeseries(days: number) {
  return useQuery<ReportsTimeseriesPoint[]>({
    queryKey: queryKeys.reportsTimeseries(days),
    queryFn: () => apiFetch<ReportsTimeseriesPoint[]>(`/admin/reports/timeseries?days=${days}`),
  });
}

/**
 * Per-campaign qualification-rate table for the reports page.
 *
 * GET /admin/reports/campaigns → ReportsCampaignRow[]
 */
export function useReportsCampaigns() {
  return useQuery<ReportsCampaignRow[]>({
    queryKey: queryKeys.reportsCampaigns(),
    queryFn: () => apiFetch<ReportsCampaignRow[]>("/admin/reports/campaigns"),
  });
}
