"use client";

import { useMemo, useState } from "react";
import { AlertTriangle, Cloud, PiggyBank, Scale, Split } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { StatCard } from "@/components/dashboard/stat-card";
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Select,
  Skeleton,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  useConfirm,
  useToast,
} from "@/components/ui";
import {
  useAdminAwsCosts,
  useAdminOrgUsage,
  useAdminOrgs,
  useCloseBillingPeriod,
} from "@/lib/hooks";
import { formatUsd } from "@/lib/format";

const POOL_CATEGORY_LABELS: Record<string, string> = {
  compute: "Compute",
  database: "Database",
  storage: "Storage",
  network: "Network",
  serverless: "Serverless",
  queue: "Queue",
  logging: "Logging / Observability",
  misc: "Miscellaneous",
};

const LICENSE_BADGE: Record<string, "success" | "danger" | "neutral"> = {
  active: "success",
  suspended: "danger",
  expired: "danger",
};

/**
 * Platform-admin owner dashboard: actual AWS spend (Cost Explorer import),
 * each organization's estimated allocated share, the reserved Shared/
 * Unallocated remainder, and a per-org usage drill-down — plus the manual
 * billing-period close action (req §13/§16). Gated the same way as every
 * other platform-admin page (see apps/web/src/app/(dashboard)/organizations/page.tsx);
 * the underlying endpoints are `verify_platform_admin`-only regardless of
 * what the frontend shows.
 */
export default function OrganizationUsagePage() {
  const { toast } = useToast();
  const confirm = useConfirm();
  const awsCosts = useAdminAwsCosts("current");
  const orgs = useAdminOrgs();
  const closePeriod = useCloseBillingPeriod();
  const [selectedOrgId, setSelectedOrgId] = useState<string>("");

  const orgUsage = useAdminOrgUsage(selectedOrgId || undefined, "current");
  const orgOptions = useMemo(() => orgs.data ?? [], [orgs.data]);

  async function handleClosePeriod() {
    const period = awsCosts.data?.billing_period;
    if (!period) return;
    const ok = await confirm({
      title: "Close billing period",
      description: `This imports actual AWS cost, aggregates usage, allocates it across every organization, generates invoices, and locks ${period}. Locked periods can't be silently reopened — late usage becomes an adjustment on a future period instead.`,
    });
    if (!ok) return;
    closePeriod.mutate(period, {
      onSuccess: (result) =>
        toast({
          title: "Billing period closed",
          description: `${result.billing_period}: ${result.invoiced_orgs} organization(s) invoiced.`,
          variant: "success",
        }),
      onError: (err) =>
        toast({ title: "Could not close period", description: err.message, variant: "error" }),
    });
  }

  const isError = awsCosts.isError;

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Organization Usage & AWS Reconciliation"
        description="Actual AWS infrastructure cost, how it's estimated across organizations, and what's left unallocated."
        action={
          <Button
            onClick={handleClosePeriod}
            disabled={!awsCosts.data?.billing_period || closePeriod.isPending}
          >
            {closePeriod.isPending ? "Closing…" : "Close billing period"}
          </Button>
        }
      />

      {!isError && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Actual AWS cost"
            value={formatUsd(awsCosts.data?.actual_aws_total)}
            icon={Cloud}
            tint="primary"
            sublabel={
              awsCosts.data?.actual_aws_total === 0
                ? "No AWS Cost Explorer import yet"
                : "From AWS Cost Explorer"
            }
          />
          <StatCard
            label="Allocated to organizations"
            value={formatUsd(awsCosts.data?.allocated_org_total)}
            icon={Split}
            tint="sky"
          />
          <StatCard
            label="Shared / Unallocated"
            value={formatUsd(awsCosts.data?.shared_unallocated_total)}
            icon={PiggyBank}
            tint="amber"
            sublabel="Overhead not attributed to any single org"
          />
          <StatCard
            label="Actual vs. allocated difference"
            value={formatUsd(awsCosts.data?.difference)}
            icon={Scale}
            tint={Math.abs(awsCosts.data?.difference ?? 0) > 0.01 ? "rose" : "emerald"}
          />
        </div>
      )}

      {isError ? (
        <div className="mt-8">
          <QueryBoundary isLoading={false} isError onRetry={() => awsCosts.refetch()}>
            <></>
          </QueryBoundary>
        </div>
      ) : (
        <>
          <Card className="mt-8">
            <CardHeader>
              <CardTitle>AWS cost pools</CardTitle>
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                {awsCosts.data?.billing_period ?? "Current period"} — allocation version{" "}
                {awsCosts.data?.allocation_version ?? "—"}
              </p>
            </CardHeader>
            <CardContent className="p-0">
              <QueryBoundary
                isLoading={awsCosts.isLoading}
                isError={false}
                loadingFallback={
                  <div className="p-4">
                    <Skeleton className="h-32 w-full" />
                  </div>
                }
              >
                <div className="overflow-x-auto">
                  <Table>
                    <thead>
                      <TableRow isHeader>
                        <TableHeader>Category</TableHeader>
                        <TableHeader>Total cost</TableHeader>
                        <TableHeader>Currency</TableHeader>
                      </TableRow>
                    </thead>
                    <tbody>
                      {(awsCosts.data?.pools ?? []).length === 0 ? (
                        <TableRow>
                          <TableCell colSpan={3} className="text-center text-slate-400">
                            No AWS cost data imported for this period yet.
                          </TableCell>
                        </TableRow>
                      ) : (
                        (awsCosts.data?.pools ?? []).map((pool) => (
                          <TableRow key={pool.pool_category}>
                            <TableCell className="font-semibold text-slate-800 dark:text-slate-100">
                              {POOL_CATEGORY_LABELS[pool.pool_category] ?? pool.pool_category}
                            </TableCell>
                            <TableCell className="tabular-nums">{formatUsd(pool.total_cost)}</TableCell>
                            <TableCell>{pool.currency}</TableCell>
                          </TableRow>
                        ))
                      )}
                    </tbody>
                  </Table>
                </div>
              </QueryBoundary>
            </CardContent>
          </Card>

          <Card className="mt-6">
            <CardHeader>
              <CardTitle>Per-organization usage</CardTitle>
              <p className="mt-1 flex items-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
                <AlertTriangle size={13} aria-hidden />
                Infrastructure figures are estimates — a documented proxy-metric
                share of shared AWS cost, not a per-tenant invoice.
              </p>
            </CardHeader>
            <CardContent>
              <div className="max-w-sm">
                <Select value={selectedOrgId} onChange={setSelectedOrgId} disabled={orgs.isLoading}>
                  <option value="">Select an organization…</option>
                  {orgOptions.map((org) => (
                    <option key={org.id} value={org.id}>
                      {org.name}
                    </option>
                  ))}
                </Select>
              </div>

              {selectedOrgId && (
                <div className="mt-4">
                  <QueryBoundary
                    isLoading={orgUsage.isLoading}
                    isError={orgUsage.isError}
                    onRetry={() => orgUsage.refetch()}
                    loadingFallback={<Skeleton className="h-32 w-full" />}
                  >
                    {orgUsage.data && (
                      <>
                        <div className="mb-3 flex flex-wrap items-center gap-3">
                          <span className="text-sm font-semibold text-slate-800 dark:text-slate-100">
                            {orgUsage.data.org_name}
                          </span>
                          <Badge variant={LICENSE_BADGE[orgUsage.data.license_status] ?? "neutral"}>
                            {orgUsage.data.license_status}
                          </Badge>
                          <span className="text-xs text-slate-500 dark:text-slate-400">
                            Allocated AWS: {formatUsd(orgUsage.data.allocated_aws_cost_total)} ·
                            {" "}Third-party: {formatUsd(orgUsage.data.third_party_cost_total)}
                          </span>
                        </div>
                        <div className="overflow-x-auto">
                          <Table>
                            <thead>
                              <TableRow isHeader>
                                <TableHeader>Service</TableHeader>
                                <TableHeader>Usage type</TableHeader>
                                <TableHeader>Quantity</TableHeader>
                                <TableHeader>Estimated cost</TableHeader>
                              </TableRow>
                            </thead>
                            <tbody>
                              {orgUsage.data.breakdown.length === 0 ? (
                                <TableRow>
                                  <TableCell colSpan={4} className="text-center text-slate-400">
                                    No usage recorded for this org this period.
                                  </TableCell>
                                </TableRow>
                              ) : (
                                orgUsage.data.breakdown.map((row) => (
                                  <TableRow key={`${row.service}-${row.usage_type}`}>
                                    <TableCell>{row.service}</TableCell>
                                    <TableCell>{row.usage_type}</TableCell>
                                    <TableCell className="tabular-nums">
                                      {row.total_quantity.toLocaleString()}
                                    </TableCell>
                                    <TableCell className="tabular-nums">
                                      {row.estimated_cost != null ? formatUsd(row.estimated_cost) : "—"}
                                    </TableCell>
                                  </TableRow>
                                ))
                              )}
                            </tbody>
                          </Table>
                        </div>
                      </>
                    )}
                  </QueryBoundary>
                </div>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
