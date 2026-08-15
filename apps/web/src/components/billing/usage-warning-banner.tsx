"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { AlertTriangle } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import {
  CREDIT_METRICS,
  isMetricExhausted,
  useBillingUsage,
  type BillingUsage,
} from "@/lib/hooks/useBilling";

/** How much of a capped credit has to be gone before we warn about it. */
const LOW_CREDIT_THRESHOLD = 0.8;

function lowOrEmptyMetric(usage: BillingUsage): { label: string; isOut: boolean } | null {
  for (const { key, label } of CREDIT_METRICS) {
    const metric = usage[key];
    if (metric.limit === null) continue;
    if (isMetricExhausted(metric)) return { label, isOut: true };
    if (metric.used / metric.limit >= LOW_CREDIT_THRESHOLD) return { label, isOut: false };
  }
  return null;
}

/**
 * Persistent top-of-page notice — mounted once in DashboardShell so it's
 * visible from every dashboard route, not just the Billing page's usage
 * bars.
 *
 * Mostly the *soft* warning, for the window where the org can still work —
 * a credit is running low but nothing has stopped yet. The instant a credit
 * actually empties, CreditExpiredModal takes over with a non-dismissible
 * full-screen gate and this banner is never seen underneath it.
 *
 * The "used up" branch below is therefore only reachable on /billing*, the
 * one place the gate suppresses itself so it can't cover the checkout
 * buttons — which is exactly where the reminder is still wanted.
 *
 * The renewal-reminder case was removed from here — credits are
 * recharge-based and nothing lapses on a date (see apps/api/core/usage.py),
 * so there's no upcoming deadline to warn about. See removefeature.md §4 to
 * re-add it.
 *
 * Superusers never see this — the platform admin org is exempt from plan
 * limits entirely (see deps.py's _org_is_platform_admin_owned).
 */
export function UsageWarningBanner() {
  const { user } = useAuth();
  const usage = useBillingUsage();

  if (user?.is_superuser) return null;
  if (!usage.data) return null;

  const warning = lowOrEmptyMetric(usage.data);
  if (!warning) return null;

  if (warning.isOut) {
    return (
      <BannerShell tone="amber" title={`${warning.label} used up`} cta="Renew">
        You&apos;ve spent all your {warning.label.toLowerCase()}. The rest of your plan keeps
        working until those credits run out too.
      </BannerShell>
    );
  }

  return (
    <BannerShell tone="amber" title={`${warning.label} running low`} cta="Renew">
      You&apos;re close to using up your {warning.label.toLowerCase()}. Renew to avoid an
      interruption.
    </BannerShell>
  );
}

const TONE_STYLES = {
  red: {
    wrap: "border-red-200 bg-red-50 dark:border-red-500/20 dark:bg-red-500/[0.07]",
    accent: "bg-red-500",
    icon: "bg-red-100 text-red-600 dark:bg-red-500/15 dark:text-red-300",
    title: "text-red-900 dark:text-red-200",
    body: "text-red-700/90 dark:text-red-300/80",
    cta: "bg-red-600 text-white hover:bg-red-700",
  },
  amber: {
    wrap: "border-amber-200 bg-amber-50 dark:border-amber-500/20 dark:bg-amber-500/[0.07]",
    accent: "bg-amber-500",
    icon: "bg-amber-100 text-amber-600 dark:bg-amber-500/15 dark:text-amber-300",
    title: "text-amber-900 dark:text-amber-200",
    body: "text-amber-700/90 dark:text-amber-300/80",
    cta: "bg-amber-600 text-white hover:bg-amber-700",
  },
} as const;

function BannerShell({
  tone,
  title,
  cta,
  children,
}: {
  tone: keyof typeof TONE_STYLES;
  title: string;
  cta: string;
  children: ReactNode;
}) {
  const styles = TONE_STYLES[tone];
  return (
    <div className="px-4 pt-4 sm:px-6 lg:px-8">
      <div
        className={`relative flex items-center gap-4 overflow-hidden rounded-xl border px-4 py-3.5 shadow-sm sm:px-5 ${styles.wrap}`}
      >
        <span className={`absolute inset-y-0 left-0 w-1 ${styles.accent}`} aria-hidden />
        <span
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${styles.icon}`}
        >
          <AlertTriangle size={17} aria-hidden />
        </span>
        <span className="min-w-0 flex-1">
          <span className={`block text-sm font-semibold leading-tight ${styles.title}`}>{title}</span>
          <span className={`mt-0.5 block text-sm leading-snug ${styles.body}`}>{children}</span>
        </span>
        <Link
          href="/billing/upgrade"
          className={`ml-auto shrink-0 whitespace-nowrap rounded-lg px-3.5 py-2 text-xs font-semibold shadow-sm transition-colors ${styles.cta}`}
        >
          {cta}
        </Link>
      </div>
    </div>
  );
}
