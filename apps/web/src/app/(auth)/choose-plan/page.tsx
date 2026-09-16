"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { CreditCard } from "lucide-react";
import { ChoosePlanCards } from "@/components/billing/choose-plan-cards";
import { Spinner } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { useBillingStatus } from "@/lib/hooks/useBilling";

/**
 * Standalone, full-screen onboarding gate. A freshly provisioned org has no
 * plan and gets redirected here by the dashboard layout until it picks one.
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
    if (status === "authenticated" && (user?.is_superuser || (billing.data && !needsPlan))) {
      router.replace("/");
    }
  }, [status, user, billing.data, needsPlan, router]);

  if (status !== "authenticated" || billing.isLoading || !needsPlan) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-slate-50 px-6 text-slate-500 dark:bg-slate-950 dark:text-slate-400">
        <Spinner size={22} />
        <p className="text-sm">Loading your account...</p>
      </div>
    );
  }

  return (
    <main className="min-h-screen bg-slate-50 px-4 py-10 text-slate-900 dark:bg-slate-950 dark:text-slate-100 sm:px-6 lg:px-8">
      <div className="mx-auto flex w-full max-w-6xl flex-col">
        <div className="mb-8 flex flex-col items-center text-center">
          <div className="mb-4 flex h-14 w-14 items-center justify-center rounded-2xl bg-gradient-to-br from-primary-400 to-primary-600 text-white shadow-glow-lg">
            <CreditCard size={24} aria-hidden />
          </div>
          <h1 className="text-3xl font-extrabold tracking-tight text-slate-950 dark:text-white">
            Choose a plan to continue
          </h1>
          <p className="mt-2.5 max-w-xl text-sm leading-6 text-slate-500 dark:text-slate-400">
            Pick any plan (the free tier works too) to unlock the dashboard. You can change or renew
            this later from Billing.
          </p>
        </div>

        <ChoosePlanCards onPlanActivated={() => router.replace("/")} />
      </div>
    </main>
  );
}
