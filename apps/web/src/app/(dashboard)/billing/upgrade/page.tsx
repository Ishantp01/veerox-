"use client";

import { useRouter } from "next/navigation";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { ChoosePlanCards } from "@/components/billing/choose-plan-cards";
import { Skeleton } from "@/components/ui";
import { useBillingStatus, useIsOutOfCredit } from "@/lib/hooks/useBilling";

/**
 * Dedicated full-page plan picker, linked from the "Recharge"/"Change plan"
 * button on /billing. A separate route (rather than the old in-page dialog)
 * gives the plan grid room to breathe and is directly linkable/bookmarkable/
 * shareable, unlike modal state.
 */
export default function BillingUpgradePage() {
  const router = useRouter();
  const { data, isLoading, isError, error, refetch } = useBillingStatus();

  const needsRecharge = useIsOutOfCredit();

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        title={needsRecharge ? "Renew your plan" : "Change your plan"}
        description="Pick a plan below — you'll be taken through checkout to confirm. Credits last until you use them up; there's no monthly expiry."
      />

      <QueryBoundary
        isLoading={isLoading}
        isError={isError}
        error={error}
        onRetry={() => refetch()}
        loadingFallback={<Skeleton className="h-48 w-full rounded-2xl" />}
      >
        {data && (
          <ChoosePlanCards
            currentPlanCode={data.plan?.code ?? null}
            needsRecharge={needsRecharge}
            onPlanActivated={() => router.replace("/billing")}
          />
        )}
      </QueryBoundary>
    </div>
  );
}
