"use client";

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
      <div className="flex items-center gap-2 border-b border-red-500/20 bg-red-500/10 px-4 py-2.5 text-sm text-red-700 dark:text-red-300 sm:px-6 lg:px-8">
        <AlertTriangle size={16} className="shrink-0" aria-hidden />
        <span>Your plan has expired and calls/messages are paused.</span>
        <Link href="/billing" className="ml-auto shrink-0 font-medium underline underline-offset-2">
          Renew now
        </Link>
      </div>
    );
  }

  if (billing.data.billing_status === "active" && billing.data.current_period_end) {
    const msRemaining = new Date(billing.data.current_period_end).getTime() - Date.now();
    if (msRemaining > 0 && msRemaining <= RENEWAL_REMINDER_WINDOW_MS) {
      const days = daysUntil(billing.data.current_period_end);
      return (
        <div className="flex items-center gap-2 border-b border-amber-500/20 bg-amber-500/10 px-4 py-2.5 text-sm text-amber-700 dark:text-amber-300 sm:px-6 lg:px-8">
          <AlertTriangle size={16} className="shrink-0" aria-hidden />
          <span>
            Your plan renews in {days} day{days === 1 ? "" : "s"} — there&apos;s no auto-renew, so
            check out again to keep calls/messages running.
          </span>
          <Link href="/billing" className="ml-auto shrink-0 font-medium underline underline-offset-2">
            Renew now
          </Link>
        </div>
      );
    }
  }

  const exhausted = usage.data ? firstExhaustedMetric(usage.data) : null;
  if (exhausted) {
    return (
      <div className="flex items-center gap-2 border-b border-amber-500/20 bg-amber-500/10 px-4 py-2.5 text-sm text-amber-700 dark:text-amber-300 sm:px-6 lg:px-8">
        <AlertTriangle size={16} className="shrink-0" aria-hidden />
        <span>You&apos;ve used all of this month&apos;s included {exhausted}.</span>
        <Link href="/billing" className="ml-auto shrink-0 font-medium underline underline-offset-2">
          Upgrade plan
        </Link>
      </div>
    );
  }

  return null;
}
