import { useState } from "react";
import { useToast } from "@/components/ui";
import { openRazorpayCheckout } from "@/lib/razorpay";
import { useCreateCheckout, useVerifyPayment } from "@/lib/hooks/usePayments";

/** Razorpay purchase flow shared by the Plans page and the choose-plan screen. */
export function useBuyPlan() {
  const { toast } = useToast();
  const checkout = useCreateCheckout();
  const verify = useVerifyPayment();
  const [busyPlan, setBusyPlan] = useState<string | null>(null);
  // True from the moment Razorpay reports success until our server confirms it.
  const [verifying, setVerifying] = useState(false);

  async function buy(planId: string) {
    setBusyPlan(planId);
    try {
      const session = await checkout.mutateAsync(planId);
      await openRazorpayCheckout({
        key: session.key_id,
        order_id: session.order_id,
        amount: session.amount,
        currency: session.currency,
        name: "Veerox AI",
        description: session.plan_name,
        theme: { color: "#4C82F0" },
        modal: { ondismiss: () => setBusyPlan(null) },
        handler: (res) => {
          setVerifying(true);
          verify.mutate(res, {
            onSuccess: () =>
              toast({ title: "Payment successful", description: "Your plan is active.", variant: "success" }),
            onError: (err) =>
              toast({ title: "Could not verify payment", description: err.message, variant: "error" }),
            onSettled: () => {
              setBusyPlan(null);
              setVerifying(false);
            },
          });
        },
      });
    } catch (err) {
      toast({
        title: "Could not start checkout",
        description: err instanceof Error ? err.message : "Unknown error",
        variant: "error",
      });
      setBusyPlan(null);
    }
  }

  return { buy, busyPlan, verifying };
}
