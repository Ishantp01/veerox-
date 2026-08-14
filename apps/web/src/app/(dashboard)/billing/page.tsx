"use client";

import { CreditCard } from "lucide-react";
import { useRouter } from "next/navigation";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { PlanAdminTable } from "@/components/billing/plan-admin-table";
import { ChoosePlanCards } from "@/components/billing/choose-plan-cards";
import { Badge, Button, Card, CardContent, CardHeader, CardTitle, Skeleton } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { useBillingStatus, useBillingUsage, type UsageMetric } from "@/lib/hooks/useBilling";
import { useCampaigns } from "@/lib/hooks/useCampaigns";

const STATUS_BADGE: Record<string, "success" | "danger" | "neutral"> = {
  active: "success",
  trialing: "neutral",
  past_due: "danger",
  canceled: "danger",
  incomplete: "danger",
};

function UsageBar({ label, metric, unit }: { label: string; metric: UsageMetric; unit?: string }) {
  const pct = metric.limit ? Math.min(100, (metric.used / metric.limit) * 100) : 0;
  const overLimit = metric.limit !== null && metric.used >= metric.limit;
  return (
    <div>
      <div className="mb-1 flex items-baseline justify-between text-sm">
        <span className="font-medium text-slate-700 dark:text-slate-300">{label}</span>
        <span className={overLimit ? "text-red-600 dark:text-red-400" : "text-slate-500 dark:text-slate-400"}>
          {Math.round(metric.used).toLocaleString()}
          {metric.limit !== null ? ` / ${metric.limit.toLocaleString()}` : ""} {unit}
        </span>
      </div>
      <div className="h-2 w-full overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
        <div
          className={`h-full rounded-full ${overLimit ? "bg-red-500" : "bg-primary-500"}`}
          style={{ width: `${metric.limit ? pct : 100}%` }}
        />
      </div>
    </div>
  );
}

export default function BillingPage() {
  const { data, isLoading, isError, error, refetch } = useBillingStatus();
  const usage = useBillingUsage();
  const campaigns = useCampaigns();
  const { user } = useAuth();
  const router = useRouter();

  // "Lapsed" statuses — the org has a plan on record but its last paid
  // period didn't renew (Razorpay Orders never auto-renew; see
  // apps/api/routers/billing.py). Distinct from "trialing", which has never
  // had a paid period to lapse in the first place.
  const needsRenewal =
    data !== undefined &&
    data.plan !== null &&
    ["past_due", "canceled", "incomplete"].includes(data.billing_status);

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        title="Billing"
        description="Current plan, usage, and subscription management."
      />

      <QueryBoundary
        isLoading={isLoading}
        isError={isError}
        error={error}
        onRetry={() => refetch()}
        loadingFallback={<Skeleton className="h-48 w-full rounded-2xl" />}
      >
        {data && (
          <div className="flex flex-col gap-6">
            {user?.is_superuser ? (
              // Platform admin's own org is unlimited and doesn't buy a
              // plan — showing "Choose plan" cards or usage bars here would
              // just be confusing (or, worse, look like it's over its own
              // limits, which it's exempt from). Only the catalog editor
              // is relevant to this account.
              <Card>
                <CardContent className="flex items-center gap-2 py-4 text-sm text-slate-600 dark:text-slate-400">
                  <CreditCard size={16} aria-hidden />
                  Platform admin account — unlimited, not billed. Manage the plan catalog below.
                </CardContent>
              </Card>
            ) : (
              <>
                <Card className="overflow-hidden">
                  <CardHeader className="flex flex-row items-center justify-between">
                    <CardTitle className="flex items-center gap-2.5">
                      <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-primary-400 to-primary-600 text-white shadow-glow">
                        <CreditCard size={15} aria-hidden />
                      </span>
                      Current plan
                    </CardTitle>
                    <Badge variant={STATUS_BADGE[data.billing_status] ?? "neutral"}>
                      {data.billing_status.replace("_", " ")}
                    </Badge>
                  </CardHeader>
                  <CardContent className="flex flex-col gap-4">
                    <div>
                      <p className="text-2xl font-extrabold tracking-tight text-slate-900 dark:text-slate-100">
                        {data.plan?.name ?? "No plan assigned"}
                      </p>
                      {data.current_period_end && (
                        <p
                          className={`mt-1 text-xs ${needsRenewal ? "font-medium text-red-600 dark:text-red-400" : "text-slate-500 dark:text-slate-400"}`}
                        >
                          {needsRenewal
                            ? `Access ended ${new Date(data.current_period_end).toLocaleDateString()} — renew to restore calls and messages.`
                            : `Access until ${new Date(data.current_period_end).toLocaleDateString()} — no auto-renew, check out again to extend.`}
                        </p>
                      )}
                    </div>
                    {data.plan && (
                      <div>
                        <Button
                          variant={needsRenewal ? "danger" : "primary"}
                          onClick={() => router.push("/billing/upgrade")}
                        >
                          {needsRenewal ? "Renew plan" : "Upgrade plan"}
                        </Button>
                      </div>
                    )}
                  </CardContent>
                </Card>

                {usage.data && (
                  <Card>
                    <CardHeader>
                      <CardTitle>Usage this month</CardTitle>
                    </CardHeader>
                    <CardContent className="flex flex-col gap-4">
                      {data.plan && (
                        <UsageBar
                          label="Team members"
                          metric={{
                            used: data.seat_count,
                            limit: typeof data.plan.limits.max_seats === "number"
                              ? data.plan.limits.max_seats
                              : null,
                          }}
                        />
                      )}
                      <UsageBar label="Call minutes" metric={usage.data.call_minutes} unit="min" />
                      <UsageBar label="WhatsApp messages" metric={usage.data.whatsapp_messages} />
                      {data.plan && campaigns.data && (
                        <UsageBar
                          label="Campaigns"
                          metric={{
                            used: campaigns.data.length,
                            limit:
                              typeof data.plan.limits.max_campaigns === "number"
                                ? data.plan.limits.max_campaigns
                                : null,
                          }}
                        />
                      )}
                    </CardContent>
                  </Card>
                )}

                {/* With a plan already active the catalog lives on the
                    dedicated /billing/upgrade page behind the button above —
                    showing every plan inline under the current one reads
                    like the org hasn't picked yet. With no plan assigned
                    there's nothing to hide it behind, so the cards stay on
                    the page (defensive: (dashboard)/layout.tsx normally
                    redirects a plan-less org to /choose-plan first). */}
                {!data.plan && <ChoosePlanCards currentPlanCode={null} />}
              </>
            )}

            {user?.is_superuser && (
              <>
                <PlanAdminTable />
                {/* HelpDeskScriptPanel and SocialLinksPanel removed from here — see removefeature.md to re-add. */}
              </>
            )}
          </div>
        )}
      </QueryBoundary>
    </div>
  );
}
