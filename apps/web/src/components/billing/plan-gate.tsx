"use client";

import { ChoosePlanScreen } from "@/components/billing/choose-plan-screen";
import { Spinner } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { usePaymentStatus } from "@/lib/hooks/usePayments";

/**
 * Plan gate: when the API says this org has no plan yet and one is required
 * (GET /payments/status -> plan_required), render the full-screen plan picker
 * instead of the dashboard. Independent of the license gate.
 *
 * While the status is still loading, a loader is shown instead of the
 * dashboard, so an org that must pick a plan never sees the dashboard flash
 * first. The owner (superuser) org is exempt, so it skips the wait. A failed
 * request never blocks the app.
 */
export function PlanGate({ children }: { children: React.ReactNode }) {
  const { user } = useAuth();
  const exempt = !!user?.is_superuser;
  const { data, isLoading } = usePaymentStatus(!exempt);

  if (exempt) return <>{children}</>;
  if (isLoading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-slate-50 dark:bg-canvas-950">
        <Spinner size={28} label="Loading" className="text-slate-500 dark:text-white" />
      </div>
    );
  }
  if (data?.plan_required) return <ChoosePlanScreen />;
  return <>{children}</>;
}
