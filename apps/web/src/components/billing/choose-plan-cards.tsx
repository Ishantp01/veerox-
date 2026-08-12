"use client";

import { Card, CardContent, CardHeader, CardTitle, Button, useToast } from "@/components/ui";
import {
  useAvailablePlans,
  useCreateCheckoutSession,
  useVerifyPayment,
} from "@/lib/hooks/useBilling";
import { openRazorpayCheckout } from "@/lib/razorpay";
import { useQueryClient } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { useState } from "react";

function formatPriceLabel(cents: number): string {
  return cents === 0 ? "Free" : `₹${(cents / 100).toLocaleString("en-IN")}/mo`;
}

// Limit keys rendered as the plan's feature list, in display order. Anything
// not listed here (a key added to the catalog later) still shows up via the
// fallback below, just with its raw key humanised.
const LIMIT_LABELS: Record<string, string> = {
  max_seats: "team members",
  max_campaigns: "campaigns",
  max_call_minutes_per_month: "call minutes / month",
  max_whatsapp_messages_per_month: "WhatsApp messages / month",
  automated_followups: "automated follow-ups",
};

function formatLimitValue(value: unknown): string {
  if (value === null || value === undefined) return "Unlimited";
  if (typeof value === "number") return value.toLocaleString("en-IN");
  if (typeof value === "boolean") return "";
  return String(value);
}

function planFeatures(
  limits: Record<string, unknown>
): { key: string; text: string; included: boolean }[] {
  const ordered = [
    ...Object.keys(LIMIT_LABELS).filter((k) => k in limits),
    ...Object.keys(limits).filter((k) => !(k in LIMIT_LABELS)),
  ];
  return ordered.map((key) => {
    const value = limits[key];
    const label = LIMIT_LABELS[key] ?? key.replace(/_/g, " ");
    // Boolean feature flags (e.g. automated_followups) read as a plain
    // label — "Automated follow-ups" / struck-through — not "true campaigns".
    const text = typeof value === "boolean" ? label : `${formatLimitValue(value)} ${label}`;
    return { key, text, included: value !== false };
  });
}

/**
 * The plan-picker grid, shared by the full Billing page (upgrading/renewing
 * an already-active org) and the standalone /choose-plan onboarding gate
 * (a freshly provisioned org with no plan yet — see apps/web/src/app/
 * (dashboard)/layout.tsx's needsPlan redirect). Kept as one component so
 * checkout/payment-verification behavior can't drift between the two.
 */
export function ChoosePlanCards({
  currentPlanCode,
  needsRenewal = false,
  onPlanActivated,
}: {
  currentPlanCode?: string | null;
  /** True when the current plan's paid period has lapsed (billing_status
   * isn't "active") — the current-plan card stays clickable and reads
   * "Renew plan" instead of the normal disabled "Current plan", since
   * Razorpay Orders don't auto-renew and re-checkout is how an org clears
   * past_due (see apps/api/routers/billing.py). */
  needsRenewal?: boolean;
  onPlanActivated?: () => void;
}) {
  const availablePlans = useAvailablePlans();
  const checkout = useCreateCheckoutSession();
  const verifyPayment = useVerifyPayment();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  // Which plan the user actually clicked. `checkout.isPending` is a single
  // flag shared by every card, so driving each button's spinner off it puts
  // all of them in a loading state at once. This also stays set across the
  // Razorpay modal — which lives outside the mutation's lifecycle — so the
  // button keeps spinning until payment is verified or the modal is closed.
  const [pendingCode, setPendingCode] = useState<string | null>(null);

  function handleChoose(planCode: string) {
    setPendingCode(planCode);
    checkout.mutate(planCode, {
      onSuccess: (session) => {
        if (session.checkout_url) {
          // Free plan — already activated server-side.
          queryClient.invalidateQueries({ queryKey: ["billing"] });
          setPendingCode(null);
          onPlanActivated?.();
          return;
        }
        if (!session.order_id || !session.razorpay_key_id) {
          setPendingCode(null);
          toast({ title: "Could not start checkout", description: "Unexpected response", variant: "error" });
          return;
        }
        openRazorpayCheckout({
          key: session.razorpay_key_id,
          order_id: session.order_id,
          amount: session.amount_cents ?? undefined,
          currency: "INR",
          name: "Veerox",
          description: `${planCode} plan`,
          // Without this, closing the Razorpay modal without paying leaves
          // the button spinning forever — the checkout mutation has already
          // settled, so nothing else would ever clear pendingCode.
          modal: { ondismiss: () => setPendingCode(null) },
          handler: (response) => {
            verifyPayment.mutate(response, {
              onSuccess: () => {
                setPendingCode(null);
                toast({ title: "Payment confirmed — plan activated", variant: "success" });
                queryClient.invalidateQueries({ queryKey: ["billing"] });
                onPlanActivated?.();
              },
              onError: (err) => {
                setPendingCode(null);
                toast({ title: "Payment verification failed", description: err.message, variant: "error" });
              },
            });
          },
        }).catch((err: Error) => {
          setPendingCode(null);
          toast({ title: "Could not open payment window", description: err.message, variant: "error" });
        });
      },
      onError: (err) => {
        setPendingCode(null);
        toast({ title: "Could not start checkout", description: err.message, variant: "error" });
      },
    });
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      {(availablePlans.data ?? []).map((plan) => {
        const isCurrent = currentPlanCode === plan.code;
        // The current plan's card stays clickable as "Renew plan" even
        // while still active — Razorpay Orders don't auto-renew (see
        // apps/api/routers/billing.py), so re-checkout is the only way to
        // extend before `current_period_end`, not just to clear past_due.
        const isLapsed = isCurrent && needsRenewal;
        return (
          <Card
            key={plan.code}
            // h-full + flex so every card in the row matches the tallest
            // one and the CTA still lines up despite uneven feature lists.
            className={`flex h-full flex-col ${isCurrent ? "ring-2 ring-primary-500" : ""}`}
          >
            <CardHeader>
              <CardTitle>{plan.name}</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-1 flex-col gap-3">
              <p className="text-lg font-bold text-slate-900 dark:text-slate-100">
                {formatPriceLabel(plan.price_cents_monthly)}
              </p>
              <ul className="flex flex-col gap-1.5 text-sm text-slate-600 dark:text-slate-400">
                {planFeatures(plan.limits).map((feature) => (
                  <li
                    key={feature.key}
                    className={`flex items-start gap-2 ${
                      feature.included ? "" : "text-slate-400 dark:text-slate-600"
                    }`}
                  >
                    {feature.included ? (
                      <Check size={14} aria-hidden className="mt-0.5 shrink-0 text-primary-500" />
                    ) : (
                      <X size={14} aria-hidden className="mt-0.5 shrink-0 text-slate-300 dark:text-slate-700" />
                    )}
                    <span>{feature.text}</span>
                  </li>
                ))}
              </ul>
              <Button
                className="mt-auto"
                variant={isLapsed ? "danger" : isCurrent ? "secondary" : "primary"}
                // Only the clicked card spins; the others just go inert so a
                // second plan can't be started while one is mid-checkout.
                // The current plan stays clickable (renew), not disabled.
                disabled={pendingCode !== null && pendingCode !== plan.code}
                loading={pendingCode === plan.code}
                onClick={() => handleChoose(plan.code)}
              >
                {isCurrent ? "Renew plan" : "Choose plan"}
              </Button>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
