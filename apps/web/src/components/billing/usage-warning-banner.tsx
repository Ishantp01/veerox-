"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { AlertTriangle } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useBillingStatus, useBillingUsage, type BillingUsage } from "@/lib/hooks/useBilling";

/** Keys here must stay in sync with BillingUsageOut in apps/api/schemas/billing.py. */
const METRIC_LABELS: Record<string, string> = {
  call_minutes: "call minutes",
  whatsapp_messages: "WhatsApp messages",
};

// Mirrors apps/api/workers/billing_expiry.py's _REMINDER_WINDOW — that
// worker only logs the approaching-expiry case server-side; this is the
// user-facing half of the same window.
const RENEWAL_REMINDER_WINDOW_MS = 3 * 24 * 60 * 60 * 1000;

function daysUntil(iso: string): number {
  return Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / (24 * 60 * 60 * 1000)));
}

function firstExhaustedMetric(usage: BillingUsage): string | null {
  for (const key of ["call_minutes", "whatsapp_messages"] as const) {
    const metric = usage[key];
    if (metric && metric.limit !== null && metric.used >= metric.limit) return METRIC_LABELS[key];
  }
  return null;
}

/**
 * Persistent top-of-page notice — mounted once in DashboardShell so it's
 * visible from every dashboard route, not just the Billing page's usage
 * bars. Three cases, in priority order:
 *   1. billing_status "past_due" — the org's paid period ended (see
 *      apps/api/workers/billing_expiry.py) and outbound calls/messages are
 *      actually blocked now (enforce_plan_limit / campaign_dialer.py /
 *      whatsapp_dispatcher.py).
 *   2. Still "active" but current_period_end is within the reminder window
 *      — Razorpay Orders never auto-renew, so this is the only warning an
 *      org gets before falling into case 1.
 *   3. Still "active" but this month's plan quota for some metric is
 *      already used up — same blocking behavior, different cause (usage,
 *      not expiry).
 * Superusers never see this — the platform admin org is exempt from plan
 * limits entirely (see deps.py's _org_is_platform_admin_owned).
 */
export function UsageWarningBanner() {
  const { user } = useAuth();
  const billing = useBillingStatus();
  const usage = useBillingUsage();

  if (user?.is_superuser) return null;
  if (!billing.data) return null;

  if (billing.data.billing_status === "past_due") {
    return (
      <BannerShell tone="red" title="Plan expired" cta="Renew now">
        Your plan has expired and calls/messages are paused.
      </BannerShell>
    );
  }

  if (billing.data.billing_status === "active" && billing.data.current_period_end) {
    const msRemaining = new Date(billing.data.current_period_end).getTime() - Date.now();
    if (msRemaining > 0 && msRemaining <= RENEWAL_REMINDER_WINDOW_MS) {
      const days = daysUntil(billing.data.current_period_end);
      return (
        <BannerShell tone="amber" title="Renewal reminder" cta="Renew now">
          Your plan renews in {days} day{days === 1 ? "" : "s"} — there&apos;s no auto-renew, so
          check out again to keep calls/messages running.
        </BannerShell>
      );
    }
  }

  const exhausted = usage.data ? firstExhaustedMetric(usage.data) : null;
  if (exhausted) {
    return (
      <BannerShell tone="amber" title={`${exhausted} limit reached`} cta="Upgrade plan">
        You&apos;ve used all of this month&apos;s included {exhausted}. Everything else on your
        plan keeps working as normal.
      </BannerShell>
    );
  }

  return null;
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
          href="/billing"
          className={`ml-auto shrink-0 whitespace-nowrap rounded-lg px-3.5 py-2 text-xs font-semibold shadow-sm transition-colors ${styles.cta}`}
        >
          {cta}
        </Link>
      </div>
    </div>
  );
}
