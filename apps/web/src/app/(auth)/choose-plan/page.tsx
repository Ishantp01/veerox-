"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { CreditCard } from "lucide-react";
import { ChoosePlanCards } from "@/components/billing/choose-plan-cards";
import { useAuth } from "@/lib/auth-context";
import { useBillingStatus } from "@/lib/hooks/useBilling";

/**
 * Standalone, full-screen onboarding gate — no sidebar/topbar (lives in the
 * (auth) route group precisely to opt out of the dashboard shell, same as
 * /login). A freshly provisioned org has no plan (`Org.plan_id` is null —
 * see apps/api/routers/auth.py's provision_org) and gets redirected here by
 * (dashboard)/layout.tsx's needsPlan check on every other route, so this is
 * the only thing they can reach until they pick a plan — no dashboard chrome
 * or feature pages peeking through in the meantime.
 */
export default function ChoosePlanPage() {
  const router = useRouter();
  const { status, user } = useAuth();
  const billing = useBillingStatus();

  const needsPlan = billing.data !== undefined && billing.data.plan === null;

  useEffect(() => {
    if (status === "unauthenticated") {
      router.replace("/login");
      return;
    }
    // Already has a plan (or is the unlimited platform-admin org) — nothing
    // to gate on, send them into the real dashboard.
    if (status === "authenticated" && (user?.is_superuser || (billing.data && !needsPlan))) {
      router.replace("/");
    }
  }, [status, user, billing.data, needsPlan, router]);

  if (status !== "authenticated" || billing.isLoading || !needsPlan) return null;

  return (
    <div className="w-full max-w-3xl">
      <div className="flex flex-col items-center mb-8 text-center">
        <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-gradient-to-br from-primary-400 to-primary-600 text-white mb-4 shadow-glow-lg">
          <CreditCard size={22} aria-hidden />
        </div>
        <h1 className="text-2xl font-extrabold text-white tracking-tight">Choose a plan to continue</h1>
        <p className="text-sm text-slate-400 mt-2 max-w-md">
          Pick any plan (the free tier works too) to unlock the dashboard — you can change or renew
          this later from Billing.
        </p>
      </div>

      <ChoosePlanCards onPlanActivated={() => router.replace("/")} />
    </div>
  );
}
