"use client";

import { Activity, Cloud, CreditCard, Layers } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { StatCard } from "@/components/dashboard/stat-card";
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Skeleton,
  Table,
  TableCell,
  TableHeader,
  TableRow,
} from "@/components/ui";
import { useOrgBillingEstimate, useOrgUsage } from "@/lib/hooks";
import { formatUsd } from "@/lib/format";

// Human-friendly labels for the fixed service/usage_type vocabulary — see
// apps/api/db/models/usage_event.py's USAGE_SERVICES / USAGE_TYPES.
const SERVICE_LABELS: Record<string, string> = {
  whatsapp: "WhatsApp",
  voice: "AI Calling",
  ai: "AI / OpenAI",
  compute: "Compute (AWS)",
  database: "Database (AWS)",
  storage: "Storage (AWS)",
  network: "Network (AWS)",
  queue: "Queue (AWS)",
  other: "Other",
};

const USAGE_TYPE_LABELS: Record<string, string> = {
  whatsapp_messages: "Messages",
  voice_seconds: "Call seconds",
  ai_input_tokens: "Input tokens",
  ai_output_tokens: "Output tokens",
  ai_request_count: "Requests",
  worker_job_seconds: "Worker seconds",
  cpu_ms: "CPU (ms)",
  memory_mb_ms: "Memory (MB·ms)",
  storage_gb_month: "Storage (GB-month)",
  bandwidth_gb: "Bandwidth (GB)",
  database_operations: "DB operations",
  invocation_count: "Invocations",
};

const STATUS_BADGE: Record<string, "success" | "live" | "neutral"> = {
  locked: "success",
  calculated: "neutral",
  pending: "live",
};

/**
 * Org self-service usage dashboard — current billing period's usage across
 * every channel, the shared-AWS-infrastructure estimate, platform fee, and
 * a short billing history. Scoped entirely server-side to the caller's own
 * org (GET /org/usage, /org/billing/estimate — see apps/api/routers/usage.py's
 * RequestOrgDep); there is no org-id input anywhere on this page.
 *
 * The AWS component is always labeled "Estimated Infrastructure Usage" —
 * never "AWS invoice" — because the underlying infrastructure is shared
 * across every organization and this org's share is a documented proxy-
 * metric allocation, not an exact per-tenant bill (req §10/§12).
 */
export default function UsagePage() {
  const usage = useOrgUsage("current");
  const estimate = useOrgBillingEstimate("current");
  const isError = usage.isError || estimate.isError;

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Usage & Billing"
        description="Your organization's usage this billing period, and an estimate of what it costs."
      />

      {!isError && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <StatCard
            label="Billing period"
            value={usage.data?.billing_period ?? "—"}
            icon={CreditCard}
            tint="primary"
          />
          <StatCard
            label="Third-party usage cost"
            value={formatUsd(usage.data?.third_party_cost_total)}
            icon={Activity}
            tint="sky"
            sublabel="OpenAI, Meta/WhatsApp, Plivo, Twilio — your own provider accounts"
          />
          <StatCard
            label="Estimated infrastructure usage"
            value={formatUsd(usage.data?.estimated_infrastructure_usage)}
            icon={Cloud}
            tint="purple"
            sublabel="Your share of shared AWS costs — an estimate, not an invoice"
          />
          <StatCard
            label="Estimated total charge"
            value={formatUsd(usage.data?.estimated_total_charge)}
            icon={Layers}
            tint="emerald"
            sublabel={`Platform fee: ${formatUsd(usage.data?.platform_fee)}`}
          />
        </div>
      )}

      {isError ? (
        <div className="mt-8">
          <QueryBoundary
            isLoading={false}
            isError
            onRetry={() => {
              usage.refetch();
              estimate.refetch();
            }}
          >
            <></>
          </QueryBoundary>
        </div>
      ) : (
        <>
          <Card className="mt-8">
            <CardHeader>
              <CardTitle>Usage breakdown</CardTitle>
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                Every metered service/usage type for{" "}
                {usage.data?.billing_period ?? "the current period"}
                {usage.data?.calculation_status && (
                  <>
                    {" — "}
                    <Badge variant={STATUS_BADGE[usage.data.calculation_status] ?? "neutral"}>
                      {usage.data.calculation_status}
                    </Badge>
                  </>
                )}
              </p>
            </CardHeader>
            <CardContent className="p-0">
              <QueryBoundary
                isLoading={usage.isLoading}
                isError={false}
                loadingFallback={
                  <div className="p-4">
                    <Skeleton className="h-40 w-full" />
                  </div>
                }
              >
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
                      {(usage.data?.breakdown ?? []).length === 0 ? (
                        <TableRow>
                          <TableCell colSpan={4} className="text-center text-slate-400">
                            No usage recorded yet this period.
                          </TableCell>
                        </TableRow>
                      ) : (
                        (usage.data?.breakdown ?? []).map((row) => (
                          <TableRow key={`${row.service}-${row.usage_type}`}>
                            <TableCell className="font-semibold text-slate-800 dark:text-slate-100">
                              {SERVICE_LABELS[row.service] ?? row.service}
                            </TableCell>
                            <TableCell>{USAGE_TYPE_LABELS[row.usage_type] ?? row.usage_type}</TableCell>
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
              </QueryBoundary>
            </CardContent>
          </Card>

          <Card className="mt-6">
            <CardHeader>
              <CardTitle>Billing history</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <QueryBoundary
                isLoading={estimate.isLoading}
                isError={false}
                loadingFallback={
                  <div className="p-4">
                    <Skeleton className="h-24 w-full" />
                  </div>
                }
              >
                <div className="overflow-x-auto">
                  <Table>
                    <thead>
                      <TableRow isHeader>
                        <TableHeader>Period</TableHeader>
                        <TableHeader>Usage charge</TableHeader>
                        <TableHeader>Platform fee</TableHeader>
                        <TableHeader>Total</TableHeader>
                        <TableHeader>Status</TableHeader>
                      </TableRow>
                    </thead>
                    <tbody>
                      {(estimate.data?.history ?? []).length === 0 ? (
                        <TableRow>
                          <TableCell colSpan={5} className="text-center text-slate-400">
                            No past billing periods yet.
                          </TableCell>
                        </TableRow>
                      ) : (
                        (estimate.data?.history ?? []).map((row) => (
                          <TableRow key={row.billing_period}>
                            <TableCell className="font-semibold text-slate-800 dark:text-slate-100">
                              {row.billing_period}
                            </TableCell>
                            <TableCell className="tabular-nums">{formatUsd(row.usage_charge)}</TableCell>
                            <TableCell className="tabular-nums">{formatUsd(row.platform_fee)}</TableCell>
                            <TableCell className="tabular-nums">{formatUsd(row.total_amount)}</TableCell>
                            <TableCell>
                              <Badge variant={row.status === "final" ? "success" : "neutral"}>
                                {row.status}
                              </Badge>
                            </TableCell>
                          </TableRow>
                        ))
                      )}
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
