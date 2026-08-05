"use client";

import { useRouter } from "next/navigation";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { ChoosePlanCards } from "@/components/billing/choose-plan-cards";
import { Skeleton } from "@/components/ui";
import { useBillingStatus } from "@/lib/hooks/useBilling";

/**
 * Dedicated full-page plan picker, linked from the "Upgrade plan"/"Renew
 * plan" button on /billing. A separate route (rather than the old in-page
 * dialog) gives the plan grid room to breathe and is directly linkable/
 * bookmarkable/shareable, unlike modal state.
 */
export default function BillingUpgradePage() {
  const router = useRouter();
  const { data, isLoading, isError, error, refetch } = useBillingStatus();

  const needsRenewal =
    data !== undefined &&
    data.plan !== null &&
    ["past_due", "canceled", "incomplete"].includes(data.billing_status);

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        title={needsRenewal ? "Renew your plan" : "Upgrade your plan"}
        description="Pick a plan below — you'll be taken through checkout to confirm."
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
            needsRenewal={needsRenewal}
            onPlanActivated={() => router.replace("/billing")}
          />
        )}
      </QueryBoundary>
    </div>
  );
}
