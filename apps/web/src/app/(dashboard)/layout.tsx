"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { DashboardShell } from "@/components/layout/dashboard-shell";
import { Spinner } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { useBillingStatus } from "@/lib/hooks/useBilling";

function FullScreenLoader() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 dark:bg-slate-950">
      <Spinner size={28} label="Loading" />
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
 */
const MEMBER_RESTRICTED_PREFIXES = ["/team", "/settings", "/billing"];

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
    }
  }, [status, needsPlan, isRestrictedMember, onRestrictedRoute, router]);

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

  return <DashboardShell>{children}</DashboardShell>;
}
