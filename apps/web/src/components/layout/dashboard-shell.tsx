"use client";

import { useState, type ReactNode } from "react";
import Nav from "@/components/nav";
import { Topbar } from "@/components/layout/topbar";
import { UsageWarningBanner } from "@/components/billing/usage-warning-banner";
import { CreditExpiredModal } from "@/components/billing/credit-expired-modal";
import { EmergencyEscalationPopup } from "@/components/escalations/emergency-escalation-popup";

/**
 * Owns the mobile-drawer open state shared by Nav (the sidebar itself) and
 * Topbar (the hamburger button that opens it). Below the `lg` breakpoint the
 * sidebar is off-canvas by default; at `lg` and up Nav ignores this state and
 * renders statically (see nav.tsx's `lg:translate-x-0`).
 */
export function DashboardShell({ children }: { children: ReactNode }) {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  return (
    <div className="flex h-screen overflow-hidden bg-mesh-light dark:bg-mesh-dark">
      <Nav mobileOpen={mobileNavOpen} onCloseMobile={() => setMobileNavOpen(false)} />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Topbar onMenuClick={() => setMobileNavOpen(true)} />
        <UsageWarningBanner />
        <main data-tour="page-root" className="flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8">
          {children}
        </main>
      </div>
      {/* Out-of-credit / billing-lapsed recharge prompt. Mounted here (not
          per page) so it reaches every dashboard route, and outside <main>
          so its backdrop — which deliberately covers the sidebar too —
          isn't clipped by the scroll container. Dismissible, but re-opens
          on its own every SNOOZE_MS until the org recharges. */}
      <CreditExpiredModal />
      {/* Non-blocking corner alert for a brand-new, unclaimed
          transfer_to_human escalation — same "outside <main>, reaches every
          route" rationale as CreditExpiredModal above, but deliberately a
          small card (not a full-screen takeover) so it doesn't stop
          whatever the team member is doing. */}
      <EmergencyEscalationPopup />
    </div>
  );
}

export default DashboardShell;
