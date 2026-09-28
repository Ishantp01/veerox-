import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

// Mirrors apps/api/schemas/usage.py's UsageBreakdownRow.
export interface UsageBreakdownRow {
  service: string;
  usage_type: string;
  total_quantity: number;
  estimated_cost: number | null;
}

// Mirrors apps/api/schemas/usage.py's OrgUsageOut.
export interface OrgUsage {
  org_id: string;
  billing_period: string;
  breakdown: UsageBreakdownRow[];
  third_party_cost_total: number;
  // Always labeled "estimated" — a proxy-metric share of shared AWS
  // infrastructure cost, never an exact per-tenant invoice (req §10/§12).
  // See the page's own "Estimated Infrastructure Usage" label.
  estimated_infrastructure_usage: number;
  platform_fee: number;
  estimated_total_charge: number;
  calculation_status: "pending" | "calculated" | "locked";
}

// Mirrors apps/api/schemas/usage.py's BillingHistoryRow.
export interface BillingHistoryRow {
  billing_period: string;
  usage_charge: number;
  platform_fee: number;
  total_amount: number;
  status: "draft" | "final" | "void";
}

// Mirrors apps/api/schemas/usage.py's OrgBillingEstimateOut.
export interface OrgBillingEstimate {
  org_id: string;
  billing_period: string;
  usage_charge_estimate: number;
  estimated_infrastructure_usage: number;
  platform_fee: number;
  estimated_total_charge: number;
  history: BillingHistoryRow[];
}

/** GET /org/usage?period=current → OrgUsage — the caller's own org only
 * (server-resolved, see apps/api/deps.py's RequestOrgDep). */
export function useOrgUsage(period: string = "current") {
  return useQuery<OrgUsage>({
    queryKey: ["org", "usage", period],
    queryFn: () => apiFetch<OrgUsage>(`/org/usage?period=${encodeURIComponent(period)}`),
  });
}

/** GET /org/billing/estimate?period=current → OrgBillingEstimate. */
export function useOrgBillingEstimate(period: string = "current") {
  return useQuery<OrgBillingEstimate>({
    queryKey: ["org", "billing-estimate", period],
    queryFn: () =>
      apiFetch<OrgBillingEstimate>(`/org/billing/estimate?period=${encodeURIComponent(period)}`),
  });
}
