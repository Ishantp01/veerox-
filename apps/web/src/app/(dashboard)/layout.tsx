"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { DashboardShell } from "@/components/layout/dashboard-shell";
import { Spinner } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { useBillingStatus } from "@/lib/hooks/useBilling";

function FullScreenLoader() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas-950 bg-mesh-dark">
      <Spinner size={28} label="Loading" className="text-white" />
    </div>
  );
}

/**
 * Authenticated shell shared by every dashboard page: fixed sidebar + topbar
 * + scrollable main. The (auth) group deliberately does NOT inherit this, so
 * the login page renders without the sidebar.
 *
 * Auth is client-side only (token lives in localStorage, checked by
 * AuthProvider), so `status` starts "loading" on every render — that state
 * renders nothing rather than a flash of the dashboard. Unauthenticated
 * visitors are redirected straight to /login (no dashboard chrome, no
 * "Forbidden"/403 ever reaches the user).
 *
 * Onboarding gate: a freshly provisioned org has no plan (`Org.plan_id` is
 * null — see apps/api/routers/auth.py's provision_org) until its admin
 * actively picks one, even the free Basic tier. Until then, every dashboard
 * route bounces to the standalone /choose-plan screen (in the (auth) route
 * group, so it renders full-screen with no sidebar/topbar — there's no
 * dashboard chrome or feature page ever visible to an unplanned account,
 * only the plan picker). The platform admin's own org is exempt (it's
 * superuser-owned, unlimited, never buys a plan) so this never gates that
 * account.
 *
 * Member restriction: a plain "member" (as opposed to admin) only
 * gets the org's working tools, not its back office — Team/Settings/Billing
 * are hidden from the sidebar (see components/nav.tsx's
 * MEMBER_RESTRICTED_HREFS) and, since a nav link is just UI, also redirected
 * away here in case a member navigates to one of those URLs directly.
 *
 * Plan-feature gating: same reasoning, but keyed off the org's plan instead
 * of the user's role — components/nav.tsx's PLAN_FEATURE_HREFS hides the
 * link, and PLAN_FEATURE_PREFIXES below redirects a direct/bookmarked
 * visit to /billing. The API enforces the same flag independently (see
 * apps/api/deps.py's enforce_plan_feature) since a route redirect only
 * stops the browser, not a direct request.
 */
const MEMBER_RESTRICTED_PREFIXES = ["/team", "/settings", "/billing"];
const PLAN_FEATURE_PREFIXES: { prefix: string; feature: string }[] = [
  { prefix: "/automation/follow-ups", feature: "automated_followups" },
];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { status, user } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const billing = useBillingStatus();

  const needsPlan =
    status === "authenticated" &&
    !user?.is_superuser &&
    billing.data !== undefined &&
    billing.data.plan === null;

  const isRestrictedMember = user?.role === "member" && !user?.is_superuser;
  const onRestrictedRoute = MEMBER_RESTRICTED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)
  );

  const gatedFeature = PLAN_FEATURE_PREFIXES.find(
    ({ prefix }) => pathname === prefix || pathname.startsWith(`${prefix}/`)
  )?.feature;
  const onGatedFeatureRoute =
    !user?.is_superuser &&
    gatedFeature !== undefined &&
    billing.data?.plan !== undefined &&
    billing.data.plan !== null &&
    billing.data.plan.limits[gatedFeature] !== true;

  useEffect(() => {
    if (status === "unauthenticated") {
      router.replace("/login");
      return;
    }
    if (needsPlan) {
      router.replace("/choose-plan");
      return;
    }
    if (isRestrictedMember && onRestrictedRoute) {
      router.replace("/");
      return;
    }
    if (onGatedFeatureRoute) {
      router.replace("/billing");
    }
  }, [status, needsPlan, isRestrictedMember, onRestrictedRoute, onGatedFeatureRoute, router]);

  // Auth status is still resolving (/auth/me in flight) — show a spinner
  // rather than a blank screen, since a slow/cold backend can leave this
  // state visible for several seconds.
  if (status === "loading") return <FullScreenLoader />;
  // Unauthenticated: about to redirect to /login, nothing to render.
  if (status !== "authenticated") return null;
  // Still resolving billing status — spinner, not a flash of the full
  // dashboard chrome for an account that turns out to need the gate.
  if (billing.isLoading) return <FullScreenLoader />;
  if (needsPlan) return null;
  if (isRestrictedMember && onRestrictedRoute) return null;
  if (onGatedFeatureRoute) return null;

  return (
    <DashboardShell>
      {children}
      {/* Help Desk chatbot widget removed from here — see removefeature.md to re-add. */}
    </DashboardShell>
  );
}
