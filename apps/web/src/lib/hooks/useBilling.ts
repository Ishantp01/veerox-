import { useMutation, useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";
import { POLL } from "@/lib/query";

export interface Plan {
  code: string;
  name: string;
  price_cents_monthly: number;
  limits: Record<string, number>;
}

export interface BillingStatus {
  billing_status: "trialing" | "active" | "past_due" | "canceled" | "incomplete";
  plan: Plan | null;
  seat_count: number;
  current_period_end: string | null;
}

export interface UsageMetric {
  used: number;
  limit: number | null;
}

export interface BillingUsage {
  period_start: string;
  call_minutes: UsageMetric;
  whatsapp_messages: UsageMetric;
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
