"use client";

import { useEffect } from "react";
import { usePathname, useRouter } from "next/navigation";
import { DashboardShell } from "@/components/layout/dashboard-shell";
import { SocialLinksFloatingBar } from "@/components/layout/social-links-floating-bar";
import { OnboardingTour } from "@/components/onboarding/onboarding-tour";
import { LicenseLockedScreen } from "@/components/organizations/license-locked-screen";
import { Spinner } from "@/components/ui";
import { useAuth } from "@/lib/auth-context";
import { featureForRoute, isFeatureDisabled } from "@/lib/orgFeatures";

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
 * License gate: once the platform admin lets an org's license expire or
 * suspends it, every dashboard route renders LicenseLockedScreen instead of
 * its normal content — see components/organizations/license-locked-screen.tsx.
 * The API enforces the same thing independently (apps/api/deps.py's
 * enforce_org_license) since this is just the browser-side reflection of it.
 * The platform admin's own org is always exempt.
 *
 * Member restriction: a plain "member" (as opposed to admin) only
 * gets the org's working tools, not its back office — Team/Settings are
 * hidden from the sidebar (see components/nav.tsx's MEMBER_RESTRICTED_HREFS)
 * and, since a nav link is just UI, also redirected away here in case a
 * member navigates to one of those URLs directly.
 *
 * Feature restriction: same idea for a module the platform admin has
 * disabled for this org (Org.enabled_features / apps/api/deps.py's
 * require_feature) — components/nav.tsx hides the sidebar item, and this
 * bounces a direct URL visit back to "/" too (see lib/orgFeatures.ts's
 * FEATURE_GATED_ROUTES).
 */
const MEMBER_RESTRICTED_PREFIXES = ["/team", "/settings"];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { status, user } = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  const isRestrictedMember = user?.role === "member" && !user?.is_superuser;
  const onRestrictedRoute = MEMBER_RESTRICTED_PREFIXES.some(
    (prefix) => pathname === prefix || pathname.startsWith(`${prefix}/`)
  );
  const routeFeature = featureForRoute(pathname);
  const onFeatureDisabledRoute =
    routeFeature !== null && isFeatureDisabled(user?.enabled_features ?? null, routeFeature);

  useEffect(() => {
    if (status === "unauthenticated") {
      router.replace("/login");
      return;
    }
    if ((isRestrictedMember && onRestrictedRoute) || onFeatureDisabledRoute) {
      router.replace("/");
    }
  }, [status, isRestrictedMember, onRestrictedRoute, onFeatureDisabledRoute, router]);

  // Auth status is still resolving (/auth/me in flight) — show a spinner
  // rather than a blank screen, since a slow/cold backend can leave this
  // state visible for several seconds.
  if (status === "loading") return <FullScreenLoader />;
  // Unauthenticated: about to redirect to /login, nothing to render.
  if (status !== "authenticated") return null;
  if ((isRestrictedMember && onRestrictedRoute) || onFeatureDisabledRoute) return null;

  const licenseInactive = user?.license_status !== "active";
  if (licenseInactive) {
    return <LicenseLockedScreen status={user!.license_status} expiresAt={user!.license_expires_at} />;
  }

  return (
    <OnboardingTour>
      <DashboardShell>
        {children}
        <SocialLinksFloatingBar />
      </DashboardShell>
    </OnboardingTour>
  );
}
