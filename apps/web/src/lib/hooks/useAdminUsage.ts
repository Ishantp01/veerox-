import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
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
