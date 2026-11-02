import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import type { UsageBreakdownRow } from "@/lib/hooks/useUsage";

// Mirrors apps/api/schemas/usage.py's AdminOrgUsageOut.
export interface AdminOrgUsage {
  org_id: string;
  org_name: string;
  license_status: string;
  billing_period: string;
  breakdown: UsageBreakdownRow[];
  allocated_aws_cost_total: number;
  third_party_cost_total: number;
}

// Mirrors apps/api/schemas/usage.py's AwsCostPoolOut.
export interface AwsCostPool {
  pool_category: string;
  total_cost: number;
  currency: string;
}

// Mirrors apps/api/schemas/usage.py's AdminAwsCostsOut.
export interface AdminAwsCosts {
  billing_period: string;
  // The real AWS bill for the period (Cost Explorer import) — see
  // apps/api/workers/aws_cost_importer.py. Zero until AWS_COST_IMPORT_ENABLED
  // is turned on and at least one period has been imported.
  actual_aws_total: number;
  // Sum of every organization's estimated allocated share.
  allocated_org_total: number;
  // The reserved remainder that couldn't be attributed to any specific org
  // (req §10) — always shown, never folded silently into actual_aws_total.
  shared_unallocated_total: number;
  difference: number;
  pools: AwsCostPool[];
  allocation_version: number | null;
}

/** GET /admin/organizations/:id/usage → AdminOrgUsage (platform-admin only). */
export function useAdminOrgUsage(orgId: string | undefined, period: string = "current") {
  return useQuery<AdminOrgUsage>({
    queryKey: ["admin", "org-usage", orgId, period],
    queryFn: () =>
      apiFetch<AdminOrgUsage>(
        `/admin/organizations/${orgId}/usage?period=${encodeURIComponent(period)}`
      ),
    enabled: Boolean(orgId),
  });
}

/** GET /admin/aws-costs?period=current → AdminAwsCosts (platform-admin only). */
export function useAdminAwsCosts(period: string = "current") {
  return useQuery<AdminAwsCosts>({
    queryKey: ["admin", "aws-costs", period],
    queryFn: () => apiFetch<AdminAwsCosts>(`/admin/aws-costs?period=${encodeURIComponent(period)}`),
  });
}

/** "YYYY-MM" for every month from `from` to `to` inclusive (empty if `from` is after `to`). */
export function monthsBetween(from: string, to: string): string[] {
  const out: string[] = [];
  let [y, m] = from.split("-").map(Number);
  const [ty, tm] = to.split("-").map(Number);
  while (y < ty || (y === ty && m <= tm)) {
    out.push(`${y}-${String(m).padStart(2, "0")}`);
    m += 1;
    if (m > 12) {
      m = 1;
      y += 1;
    }
  }
  return out;
}

function rangeLabel(periods: string[]): string {
  return periods.length > 1 ? `${periods[0]} to ${periods[periods.length - 1]}` : (periods[0] ?? "");
}

/** AWS costs for each month in `periods`, summed into one AdminAwsCosts (pools merged by category). */
export function useAdminAwsCostsRange(periods: string[]) {
  const results = useQueries({
    queries: periods.map((period) => ({
      queryKey: ["admin", "aws-costs", period],
      queryFn: () =>
        apiFetch<AdminAwsCosts>(`/admin/aws-costs?period=${encodeURIComponent(period)}`),
    })),
  });
  const loaded = results.map((r) => r.data).filter((d): d is AdminAwsCosts => Boolean(d));
  const pools = new Map<string, AwsCostPool>();
  for (const d of loaded)
    for (const p of d.pools) {
      const prev = pools.get(p.pool_category);
      pools.set(p.pool_category, { ...p, total_cost: (prev?.total_cost ?? 0) + p.total_cost });
    }
  const sum = (f: (d: AdminAwsCosts) => number) => loaded.reduce((t, d) => t + f(d), 0);
  const data: AdminAwsCosts | undefined =
    loaded.length === 0
      ? undefined
      : {
          billing_period: rangeLabel(periods),
          actual_aws_total: sum((d) => d.actual_aws_total),
          allocated_org_total: sum((d) => d.allocated_org_total),
          shared_unallocated_total: sum((d) => d.shared_unallocated_total),
          difference: sum((d) => d.difference),
          pools: [...pools.values()],
          allocation_version: periods.length === 1 ? loaded[0].allocation_version : null,
        };
  return {
    data,
    isLoading: results.some((r) => r.isLoading),
    isError: results.some((r) => r.isError),
    refetch: () => results.forEach((r) => r.refetch()),
  };
}

/** One org's usage for each month in `periods`, merged by service + usage type. */
export function useAdminOrgUsageRange(orgId: string | undefined, periods: string[]) {
  const results = useQueries({
    queries: periods.map((period) => ({
      queryKey: ["admin", "org-usage", orgId, period],
      queryFn: () =>
        apiFetch<AdminOrgUsage>(
          `/admin/organizations/${orgId}/usage?period=${encodeURIComponent(period)}`
        ),
      enabled: Boolean(orgId),
    })),
  });
  const loaded = results.map((r) => r.data).filter((d): d is AdminOrgUsage => Boolean(d));
  const rows = new Map<string, UsageBreakdownRow>();
  for (const d of loaded)
    for (const r of d.breakdown) {
      const key = `${r.service}|${r.usage_type}`;
      const prev = rows.get(key);
      rows.set(key, {
        ...r,
        total_quantity: (prev?.total_quantity ?? 0) + r.total_quantity,
        estimated_cost:
          r.estimated_cost == null && prev?.estimated_cost == null
            ? r.estimated_cost
            : (prev?.estimated_cost ?? 0) + (r.estimated_cost ?? 0),
      });
    }
  const data: AdminOrgUsage | undefined =
    loaded.length === 0
      ? undefined
      : {
          ...loaded[0],
          billing_period: rangeLabel(periods),
          breakdown: [...rows.values()],
          allocated_aws_cost_total: loaded.reduce((t, d) => t + d.allocated_aws_cost_total, 0),
          third_party_cost_total: loaded.reduce((t, d) => t + d.third_party_cost_total, 0),
        };
  return {
    data,
    isLoading: results.some((r) => r.isLoading),
    isError: results.some((r) => r.isError),
    refetch: () => results.forEach((r) => r.refetch()),
  };
}

export interface BillingPeriodCloseResult {
  billing_period: string;
  invoiced_orgs: number;
}

/** POST /admin/billing/periods/:period/close (platform-admin only). */
export function useCloseBillingPeriod() {
  const queryClient = useQueryClient();
  return useMutation<BillingPeriodCloseResult, Error, string>({
    mutationFn: (period: string) =>
      apiFetch<BillingPeriodCloseResult>(`/admin/billing/periods/${period}/close`, {
        method: "POST",
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "aws-costs"] });
      queryClient.invalidateQueries({ queryKey: ["admin", "org-usage"] });
    },
  });
}
