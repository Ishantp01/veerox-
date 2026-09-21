"use client";

import { useState, type ReactNode } from "react";
import Nav from "@/components/nav";
import { useAuth } from "@/lib/auth-context";
import { isFeatureDisabled } from "@/lib/orgFeatures";
import { Topbar } from "@/components/layout/topbar";
import { FollowUpDuePopup } from "@/components/follow-up-tasks/follow-up-due-popup";
import { EmergencyHumanSupportPopup } from "@/components/human-support/emergency-human-support-popup";

/**
 * Owns the mobile-drawer open state shared by Nav (the sidebar itself) and
 * Topbar (the hamburger button that opens it). Below the `lg` breakpoint the
 * sidebar is off-canvas by default; at `lg` and up Nav ignores this state and
 * renders statically (see nav.tsx's `lg:translate-x-0`).
 */
export function DashboardShell({ children }: { children: ReactNode }) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  // Popups poll their feature's API, which 403s for an org the platform admin
  // has switched the feature off for — so don't mount them at all then.
  const { user } = useAuth();
  const features = user?.enabled_features ?? null;

  return (
    <div className="flex h-screen overflow-hidden bg-mesh-light dark:bg-mesh-dark">
      <Nav mobileOpen={mobileNavOpen} onCloseMobile={() => setMobileNavOpen(false)} />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Topbar onMenuClick={() => setMobileNavOpen(true)} />
        <main data-tour="page-root" className="flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8">
          {children}
        </main>
      </div>
      {/* Non-blocking corner alert for a brand-new, unclaimed
          transfer_to_human humanSupport — mounted here (not per page) so it
          reaches every dashboard route, and outside <main> so it isn't
          clipped by the scroll container. Deliberately a small card (not a
          full-screen takeover) so it doesn't stop whatever the team member
          is doing. */}
      {!isFeatureDisabled(features, "human_support") && <EmergencyHumanSupportPopup />}
      {/* Bottom-right reminder for lead follow-ups that have come due —
          admins see the whole org's, members only their claimed leads
          (scoped server-side). */}
      {!isFeatureDisabled(features, "follow_up_tasks") && <FollowUpDuePopup />}
    </div>
  );
}

export default DashboardShell;
