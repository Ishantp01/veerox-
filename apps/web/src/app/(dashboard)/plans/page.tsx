"use client";

import { CalendarClock, Check, CreditCard, Lock, ReceiptText, ShieldCheck, Sparkles } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Badge,
  type BadgeVariant,
  Button,
  Card,
  EmptyState,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
} from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { PlanManager } from "./plan-manager";
import { useBuyPlan } from "@/lib/hooks/useBuyPlan";
import {
  formatPrice,
  LIMIT_FIELDS,
  type PaymentPlan,
  usePlanUsage,
  usePaymentAddons,
  usePaymentHistory,
  usePaymentPlans,
  usePaymentStatus,
} from "@/lib/hooks/usePayments";

const STATUS_BADGE: Record<string, BadgeVariant> = {
  paid: "success",
  failed: "danger",
  created: "neutral",
};

const STATUS_LABEL: Record<string, string> = {
  paid: "Paid",
  failed: "Failed",
  created: "Not completed",
};

function daysLeft(iso: string): number {
  return Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000));
}

function perDay(plan: PaymentPlan): string {
  return formatPrice(Math.round(plan.price_paise / plan.duration_days), plan.currency);
}

/** Razorpay plan purchases. Independent of the admin-managed org license. */
export default function PlansPage() {
  const { user } = useAuth();
  // The owner (platform) org publishes plans; it never buys them.
  if (user?.is_superuser) return <PlanManager />;
  return <PlansStore />;
}

function PlansStore() {
  const { user } = useAuth();
  const canBuy = user?.role === "admin" || !!user?.is_superuser;
  const plans = usePaymentPlans();
  const status = usePaymentStatus();
  const history = usePaymentHistory();
  const usage = usePlanUsage();
  const addons = usePaymentAddons(!!status.data?.active);
  const { buy, busyPlan } = useBuyPlan();

  const activePlanId = status.data?.active ? status.data.plan?.id : undefined;
  const remaining = status.data?.period_end ? daysLeft(status.data.period_end) : 0;

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Plans & Payments" description="Choose a plan and pay securely with Razorpay." />

      {/* Current plan summary */}
      <Card className="mb-8 flex flex-wrap items-center gap-4 p-5">
        <span
          className={
            "flex h-11 w-11 shrink-0 items-center justify-center rounded-xl " +
            (status.data?.active
              ? "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/15 dark:text-emerald-400"
              : "bg-slate-100 text-slate-500 dark:bg-slate-800 dark:text-slate-400")
          }
        >
          <CalendarClock size={22} aria-hidden />
        </span>
        <div className="min-w-0 flex-1">
          {status.data?.active ? (
            <>
              <p className="text-sm font-semibold text-slate-900 dark:text-slate-50">
                {status.data.plan?.name ?? "Active plan"}{" "}
                <Badge variant="success" className="ml-1.5 align-middle">
                  Active
                </Badge>
              </p>
              <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
                Valid until {new Date(status.data.period_end!).toLocaleDateString()} · {remaining}{" "}
                {remaining === 1 ? "day" : "days"} left
              </p>
            </>
          ) : (
            <>
              <p className="text-sm font-semibold text-slate-900 dark:text-slate-50">No active plan</p>
              <p className="mt-0.5 text-sm text-slate-500 dark:text-slate-400">
                Pick a plan below to get started.
              </p>
            </>
          )}
        </div>
      </Card>

      {/* Usage vs. limits for the active plan */}
      {usage.data && usage.data.length > 0 && (
        <Card className="mb-8 p-5" data-tour="plan-usage">
          <h2 className="mb-4 text-sm font-semibold text-slate-900 dark:text-slate-50">Plan usage</h2>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {LIMIT_FIELDS.map((f) => {
              const u = usage.data!.find((x) => x.resource === f.resource);
              if (!u) return null;
              const pct = u.limit ? Math.min(100, (u.used / u.limit) * 100) : 0;
              return (
                <div key={f.key}>
                  <div className="mb-1.5 flex justify-between text-xs text-slate-600 dark:text-slate-300">
                    <span>{f.label}</span>
                    <span className="font-semibold">
                      {Math.round(u.used).toLocaleString("en-IN")} / {u.limit === null ? "∞" : u.limit.toLocaleString("en-IN")}
                    </span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-slate-100 dark:bg-slate-800">
                    <div
                      className={"h-full rounded-full " + (pct >= 90 ? "bg-red-500" : pct >= 70 ? "bg-amber-500" : "bg-primary-500")}
                      style={{ width: u.limit === null ? "0%" : `${pct}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </Card>
      )}

      {/* Plans */}
      <QueryBoundary
        isLoading={plans.isLoading}
        isError={plans.isError}
        error={plans.error}
        onRetry={() => plans.refetch()}
        loadingFallback={<div className="h-48 animate-pulse rounded-2xl bg-slate-100 dark:bg-slate-800" />}
        isEmpty={plans.data?.length === 0}
        emptyFallback={
          <EmptyState icon={CreditCard} title="No plans available yet" description="Plans will appear here once published." />
        }
      >
        <div data-tour="plan-store" className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {plans.data?.map((plan) => {
            const isCurrent = plan.id === activePlanId;
            return (
              <Card
                key={plan.id}
                className={
                  "relative flex flex-col p-6 transition-shadow hover:shadow-md " +
                  (isCurrent ? "ring-2 ring-primary-500 dark:ring-primary-400" : "")
                }
              >
                {isCurrent && (
                  <Badge variant="voice" className="absolute right-4 top-4">
                    <Sparkles size={12} className="mr-1" aria-hidden /> Current
                  </Badge>
                )}
                <h2 className="text-base font-semibold text-slate-900 dark:text-slate-50">{plan.name}</h2>
                <p className="mt-3 flex items-baseline gap-1.5">
                  <span className="text-4xl font-extrabold tracking-tight text-slate-900 dark:text-slate-50">
                    {formatPrice(plan.price_paise, plan.currency)}
                  </span>
                  <span className="text-sm text-slate-500 dark:text-slate-400">/ {plan.duration_days} days</span>
                </p>
                <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">≈ {perDay(plan)} per day</p>

                <ul className="mt-5 flex-1 space-y-2.5 text-sm text-slate-600 dark:text-slate-300">
                  {plan.description && (
                    <li className="flex gap-2">
                      <Check size={16} className="mt-0.5 shrink-0 text-emerald-500" aria-hidden />
                      <span>{plan.description}</span>
                    </li>
                  )}
                  <li className="flex gap-2">
                    <Check size={16} className="mt-0.5 shrink-0 text-emerald-500" aria-hidden />
                    <span>{plan.duration_days} days of access</span>
                  </li>
                  {LIMIT_FIELDS.map((f) => (
                    <li key={f.key} className="flex gap-2">
                      <Check size={16} className="mt-0.5 shrink-0 text-emerald-500" aria-hidden />
                      <span>
                        {plan[f.key] === null ? "Unlimited" : plan[f.key]!.toLocaleString("en-IN")}{" "}
                        {f.label.toLowerCase()}
                      </span>
                    </li>
                  ))}
                </ul>

                <Button
                  size="lg"
                  variant={isCurrent ? "outline" : "primary"}
                  className="mt-6 w-full"
                  disabled={!canBuy || busyPlan !== null}
                  onClick={() => buy(plan.id)}
                >
                  {busyPlan === plan.id ? "Opening checkout…" : isCurrent ? "Renew" : "Buy plan"}
                </Button>
                {!canBuy && (
                  <p className="mt-2 flex items-center justify-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
                    <Lock size={12} aria-hidden /> Only an org admin can purchase
                  </p>
                )}
              </Card>
            );
          })}
        </div>
        <p className="mt-4 flex items-center justify-center gap-1.5 text-xs text-slate-500 dark:text-slate-400">
          <ShieldCheck size={14} aria-hidden /> Payments are processed securely by Razorpay. We never see your card details.
        </p>
      </QueryBoundary>

      {/* Add-on packs: top-ups on the running plan (e.g. extra call minutes) */}
      {status.data?.active && addons.data && addons.data.length > 0 && (
        <div data-tour="plan-addons">
          <h2 className="mb-1 mt-10 flex items-center gap-2 text-base font-semibold text-slate-900 dark:text-slate-50">
            <Sparkles size={18} aria-hidden /> Add-ons
          </h2>
          <p className="mb-3 text-sm text-slate-500 dark:text-slate-400">
            Need more of just one thing? Add it to your current plan. Everything else stays the same, and the add-on
            lasts until your current plan period ends
            {status.data.period_end ? ` (${new Date(status.data.period_end).toLocaleDateString()})` : ""}.
          </p>
          <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {addons.data.map((addon) => (
              <Card key={addon.id} className="flex flex-col p-6">
                <h3 className="text-base font-semibold text-slate-900 dark:text-slate-50">{addon.name}</h3>
                <p className="mt-2 text-3xl font-extrabold tracking-tight text-slate-900 dark:text-slate-50">
                  {formatPrice(addon.price_paise, addon.currency)}
                </p>
                {addon.description && (
                  <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">{addon.description}</p>
                )}
                <ul className="mt-4 flex-1 space-y-2 text-sm text-slate-600 dark:text-slate-300">
                  {LIMIT_FIELDS.filter((f) => addon[f.key]).map((f) => (
                    <li key={f.key} className="flex gap-2">
                      <Check size={16} className="mt-0.5 shrink-0 text-emerald-500" aria-hidden />
                      <span>
                        +{addon[f.key]!.toLocaleString("en-IN")} {f.label.toLowerCase()}
                      </span>
                    </li>
                  ))}
                </ul>
                <Button
                  size="lg"
                  className="mt-5 w-full"
                  disabled={!canBuy || busyPlan !== null}
                  onClick={() => buy(addon.id)}
                >
                  {busyPlan === addon.id ? "Opening checkout…" : "Buy add-on"}
                </Button>
              </Card>
            ))}
          </div>
        </div>
      )}

      {/* History */}
      <h2 className="mb-3 mt-10 flex items-center gap-2 text-base font-semibold text-slate-900 dark:text-slate-50">
        <ReceiptText size={18} aria-hidden /> Payment history
      </h2>
      <Card className="overflow-hidden" data-tour="plan-history">
        {history.isLoading ? (
          <Table>
            <tbody>
              <SkeletonRows rows={3} cols={4} />
            </tbody>
          </Table>
        ) : history.data?.length ? (
          <Table>
            <thead>
              <TableRow isHeader>
                <TableHeader>Date</TableHeader>
                <TableHeader>Amount</TableHeader>
                <TableHeader>Valid until</TableHeader>
                <TableHeader>Status</TableHeader>
              </TableRow>
            </thead>
            <tbody>
            {history.data.map((p) => (
              <TableRow key={p.id}>
                <TableCell>{new Date(p.created_at).toLocaleDateString()}</TableCell>
                <TableCell className="font-semibold text-slate-800 dark:text-slate-100">
                  {formatPrice(p.amount_paise, p.currency)}
                </TableCell>
                <TableCell>{p.period_end ? new Date(p.period_end).toLocaleDateString() : "—"}</TableCell>
                <TableCell>
                  <Badge variant={STATUS_BADGE[p.status] ?? "neutral"}>{STATUS_LABEL[p.status] ?? p.status}</Badge>
                </TableCell>
              </TableRow>
            ))}
            </tbody>
          </Table>
        ) : (
          <EmptyState icon={ReceiptText} title="No payments yet" description="Your payments will show up here." />
        )}
      </Card>
    </div>
  );
}
