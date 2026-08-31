"use client";

import { Badge, Card, CardContent, CardHeader, CardTitle, Button, Skeleton, useToast } from "@/components/ui";
import {
  useAvailablePlans,
  useBillingStatus,
  useCreateCheckoutSession,
  useVerifyPayment,
} from "@/lib/hooks/useBilling";
import { openRazorpayCheckout } from "@/lib/razorpay";
import { useQueryClient } from "@tanstack/react-query";
import { Check, Sparkles, X } from "lucide-react";
import { useState } from "react";

// Limit keys rendered as the plan's feature list, in display order. Anything
// not listed here (a key added to the catalog later) still shows up via the
// fallback below, just with its raw key humanised.
const LIMIT_LABELS: Record<string, string> = {
  max_seats: "team members",
  max_campaigns: "campaigns",
  max_call_minutes: "call minutes",
  max_whatsapp_messages: "WhatsApp messages",
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
  return ordered
    // A numeric limit of 0 means the plan never included that resource at
    // all (the recharge-SKU convention — see routers/billing.py), not "zero
    // remaining" — so it doesn't belong in the feature list at all, for any
    // of the four resources, not just WhatsApp.
    .filter((key) => {
      const value = limits[key];
      return !(typeof value === "number" && value === 0);
    })
    .map((key) => {
      const value = limits[key];
      const label = LIMIT_LABELS[key] ?? key.replace(/_/g, " ");
      // Boolean feature flags (e.g. automated_followups) read as a plain
      // label — "Automated follow-ups" / struck-through — not "true campaigns".
      const text = typeof value === "boolean" ? label : `${formatLimitValue(value)} ${label}`;
      return { key, text, included: value !== false };
    });
}

/**
 * The plan-picker grid, shared by the full Billing page (recharging or
 * switching plan for an already-active org) and the standalone /choose-plan onboarding gate
 * (a freshly provisioned org with no plan yet — see apps/web/src/app/
 * (dashboard)/layout.tsx's needsPlan redirect). Kept as one component so
 * checkout/payment-verification behavior can't drift between the two.
 */
export function ChoosePlanCards({
  currentPlanCode,
  needsRecharge = false,
  onPlanActivated,
}: {
  currentPlanCode?: string | null;
  /** True when the org is out of credit right now (or its billing lapsed) —
   * the current-plan card is styled as the urgent action rather than a
   * neutral "buy it again", since buying it again is the only thing that
   * restores credit (see apps/api/routers/billing.py). */
  needsRecharge?: boolean;
  onPlanActivated?: () => void;
}) {
  const availablePlans = useAvailablePlans();
  const billingStatus = useBillingStatus();
  // Free plans are one-time only per org (apps/api/routers/billing.py's
  // create_checkout_session rejects a second claim with 409) — once true,
  // every free-priced card gets locked out here too, not just on the server,
  // so the org sees why instead of hitting an error toast.
  const freePlanClaimed = billingStatus.data?.free_plan_claimed ?? false;
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

  if (availablePlans.isLoading) {
    return (
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Card key={i} className="flex h-full flex-col">
            <CardHeader>
              <Skeleton className="h-5 w-24" />
            </CardHeader>
            <CardContent className="flex flex-1 flex-col gap-4">
              <Skeleton className="h-8 w-32" />
              <div className="flex flex-col gap-2.5">
                {Array.from({ length: 4 }).map((__, j) => (
                  <Skeleton key={j} className="h-3.5 w-full max-w-[85%]" />
                ))}
              </div>
              <Skeleton className="mt-auto h-10 w-full rounded-xl" />
            </CardContent>
          </Card>
        ))}
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
      {(availablePlans.data ?? []).map((plan) => {
        const isCurrent = currentPlanCode === plan.code;
        // The current plan's card stays clickable as "Recharge" even with
        // credit left — buying the same plan again is how an org tops up
        // (it resets Org.plan_started_at, which is what usage is counted
        // from — see apps/api/core/usage.py).
        const isEmpty = isCurrent && needsRecharge;
        const isFreeLocked = plan.price_cents === 0 && freePlanClaimed;
        return (
          <Card
            key={plan.code}
            // h-full + flex so every card in the row matches the tallest
            // one and the CTA still lines up despite uneven feature lists.
            className={`relative flex h-full flex-col overflow-hidden transition-shadow ${
              isCurrent
                ? "ring-2 ring-primary-500 shadow-glow"
                : "hover:shadow-card-lg"
            }`}
          >
            {isCurrent && (
              <div className="absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-primary-400 to-primary-600" />
            )}
            <CardHeader className="flex flex-row items-center justify-between gap-2">
              <CardTitle className="flex items-center gap-2">
                <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-primary-400 to-primary-600 text-white shadow-glow">
                  <Sparkles size={14} aria-hidden />
                </span>
                {plan.name}
              </CardTitle>
              {isCurrent && (
                <Badge variant={isEmpty ? "danger" : "success"}>
                  {isEmpty ? "Out of credit" : "Current"}
                </Badge>
              )}
            </CardHeader>
            <CardContent className="flex flex-1 flex-col gap-4">
              <div>
                <p className="text-3xl font-extrabold tracking-tight text-slate-900 dark:text-slate-50">
                  {plan.price_cents === 0 ? (
                    "Free"
                  ) : (
                    <>
                      ₹{(plan.price_cents / 100).toLocaleString("en-IN")}
                      {/* One-time recharge, not a subscription — the plan
                          lasts until its credits are used up, however long
                          that takes (apps/api/core/usage.py). */}
                      <span className="text-sm font-medium text-slate-400 dark:text-slate-500">
                        {" "}
                        per renewal
                      </span>
                    </>
                  )}
                </p>
              </div>
              <div className="h-px bg-slate-100 dark:bg-slate-800" />
              <ul className="flex flex-col gap-2 text-sm text-slate-600 dark:text-slate-400">
                {planFeatures(plan.limits).map((feature) => (
                  <li
                    key={feature.key}
                    className={`flex items-start gap-2 ${
                      feature.included ? "" : "text-slate-400 dark:text-slate-600"
                    }`}
                  >
                    {feature.included ? (
                      <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-primary-50 text-primary-600 dark:bg-primary-500/10 dark:text-primary-400">
                        <Check size={11} aria-hidden />
                      </span>
                    ) : (
                      <X size={16} aria-hidden className="mt-0.5 shrink-0 text-slate-300 dark:text-slate-700" />
                    )}
                    <span>{feature.text}</span>
                  </li>
                ))}
              </ul>
              <Button
                className="mt-auto"
                variant={isEmpty ? "danger" : isCurrent ? "secondary" : "primary"}
                // Only the clicked card spins; the others just go inert so a
                // second plan can't be started while one is mid-checkout.
                // The current plan stays clickable (recharge), not disabled —
                // except a free plan already claimed once, which is locked
                // out for good regardless of whether it's the current plan.
                disabled={isFreeLocked || (pendingCode !== null && pendingCode !== plan.code)}
                loading={pendingCode === plan.code}
                onClick={() => handleChoose(plan.code)}
              >
                {isFreeLocked
                  ? "Already used"
                  : isCurrent
                    ? "Renew this plan"
                    : "Choose plan"}
              </Button>
            </CardContent>
          </Card>
        );
      })}
    </div>
  );
}
