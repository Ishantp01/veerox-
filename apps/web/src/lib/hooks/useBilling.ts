import { useMutation, useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth-context";
import { POLL } from "@/lib/query";

export interface Plan {
  code: string;
  name: string;
  price_cents: number;
  limits: Record<string, number | boolean>;
}

export interface BillingStatus {
  billing_status: "trialing" | "active" | "past_due" | "canceled" | "incomplete";
  plan: Plan | null;
  seat_count: number;
  /** When the org last recharged. Informational only — plans don't expire on
   * a timer, access ends when the credits in BillingUsage run out. */
  last_recharge_at: string | null;
}

export interface UsageMetric {
  used: number;
  limit: number | null;
}

export interface BillingUsage {
  /** Start of the credit period — the org's last recharge, not a month. */
  period_start: string;
  campaigns: UsageMetric;
  call_minutes: UsageMetric;
  whatsapp_messages: UsageMetric;
}

/**
 * The metered credits, in display order. Keys must stay in sync with
 * BillingUsageOut in apps/api/schemas/billing.py.
 */
export const CREDIT_METRICS = [
  { key: "call_minutes", label: "Call minutes", unit: "min" },
  { key: "whatsapp_messages", label: "WhatsApp messages", unit: "" },
] as const satisfies readonly { key: keyof BillingUsage; label: string; unit: string }[];

/** Billing states that block everything regardless of credits left. Nothing
 * downgrades an org on a timer any more (there's no expiry worker — see
 * apps/api/deps.py), so these only come from a failed payment or a
 * deliberate admin action. */
const LAPSED_STATUSES = ["past_due", "canceled", "incomplete"];

export function isMetricExhausted(metric: UsageMetric): boolean {
  return metric.limit !== null && metric.used >= metric.limit;
}

/**
 * True as soon as *any* metered credit is used up — that's the moment the
 * org actually loses a channel (WhatsApp credits gone means WhatsApp stops,
 * whether or not call minutes are left), so waiting for every credit to
 * empty would leave the org half-broken with no gate.
 *
 * A single zero-limit metric is excluded from that check — a `0` there just
 * means the plan never included that one channel (e.g. a WhatsApp-only plan
 * with `max_call_minutes: 0`), which the org chose on purpose, and gating
 * the whole dashboard over a channel it never had would be wrong.
 *
 * But if *every* metered channel is `0` — call minutes and WhatsApp
 * messages both — the org can't send anything at all, which is exactly the
 * "out of credit" state this gate exists for, so that case always blocks
 * regardless of usage. (A `null` limit is unlimited and never counts either
 * way.)
 */
export function isAnyCreditExhausted(usage: BillingUsage): boolean {
  if (CREDIT_METRICS.every(({ key }) => usage[key].limit === 0)) return true;
  return CREDIT_METRICS.some(({ key }) => {
    const metric = usage[key];
    return metric.limit !== null && metric.limit > 0 && isMetricExhausted(metric);
  });
}

/**
 * GET /billing/status → BillingStatus
 *
 * Polled — billing_status/plan can change from a background event (a call
 * getting force-hung-up for hitting its limit, a webhook landing) that this
 * hook has no subscription to, so waiting on the default staleTime alone
 * would leave the page showing a stale state while it's sitting open.
 */
export function useBillingStatus() {
  return useQuery<BillingStatus>({
    queryKey: ["billing", "status"],
    queryFn: () => apiFetch<BillingStatus>("/billing/status"),
    refetchInterval: POLL.billing,
    refetchOnMount: "always",
    refetchOnWindowFocus: "always",
  });
}

/**
 * GET /billing/usage → BillingUsage
 *
 * Same reasoning as `useBillingStatus` — call/message counts change from
 * background events this hook has no subscription to, so it's polled
 * rather than trusting the cache until the next mount/focus.
 */
export function useBillingUsage() {
  return useQuery<BillingUsage>({
    queryKey: ["billing", "usage"],
    queryFn: () => apiFetch<BillingUsage>("/billing/usage"),
    refetchInterval: POLL.billing,
    refetchOnMount: "always",
    refetchOnWindowFocus: "always",
  });
}

/**
 * Single source of truth for "this org can't do anything until it recharges",
 * shared by the credit gate, the warning banner, and the billing pages so
 * they can't disagree about whether an org is blocked.
 *
 * Two triggers: any one of the plan's credits is used up, or billing lapsed.
 * The platform admin's own org is exempt — it's unlimited and never buys a
 * plan (see apps/api/deps.py's _org_is_platform_admin_owned).
 */
export function useIsOutOfCredit(): boolean {
  const { user } = useAuth();
  const billing = useBillingStatus();
  const usage = useBillingUsage();

  if (user?.is_superuser) return false;
  const lapsed =
    billing.data !== undefined &&
    billing.data.plan !== null &&
    LAPSED_STATUSES.includes(billing.data.billing_status);
  return lapsed || (usage.data !== undefined && isAnyCreditExhausted(usage.data));
}

/**
 * GET /billing/available-plans → Plan[] — the real, admin-managed catalog
 * (active plans only). This is what "Choose plan" cards must render from;
 * never hardcode plan names/prices in the frontend, or they'll silently
 * drift from whatever the platform admin actually configured.
 */
export function useAvailablePlans() {
  return useQuery<Plan[]>({
    queryKey: ["billing", "available-plans"],
    queryFn: () => apiFetch<Plan[]>("/billing/available-plans"),
  });
}

export interface CheckoutSession {
  // Free plans: set — caller should just navigate here (no payment needed).
  checkout_url: string | null;
  // Paid plans: set — caller should launch Razorpay's embedded Checkout
  // modal with these for a one-time Order payment, then confirm via
  // useVerifyPayment. No public webhook URL required, which is why this
  // exists instead of a pure redirect.
  order_id: string | null;
  amount_cents: number | null;
  razorpay_key_id: string | null;
}

/** POST /billing/checkout-session → CheckoutSession */
export function useCreateCheckoutSession() {
  return useMutation<CheckoutSession, Error, string>({
    mutationFn: (planCode) =>
      apiFetch<CheckoutSession>("/billing/checkout-session", {
        method: "POST",
        body: JSON.stringify({
          plan_code: planCode,
          success_url: `${window.location.origin}/billing?checkout=success`,
          cancel_url: `${window.location.origin}/billing?checkout=cancel`,
        }),
      }),
  });
}

export interface VerifyPaymentInput {
  razorpay_payment_id: string;
  razorpay_order_id: string;
  razorpay_signature: string;
}

/** POST /billing/verify-payment — confirms payment, activates the plan. */
export function useVerifyPayment() {
  return useMutation<void, Error, VerifyPaymentInput>({
    mutationFn: (body) =>
      apiFetch<void>("/billing/verify-payment", { method: "POST", body: JSON.stringify(body) }),
  });
}
