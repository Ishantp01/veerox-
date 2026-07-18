"use client";

import { useState, type ReactNode } from "react";
import Nav from "@/components/nav";
import { Topbar } from "@/components/layout/topbar";

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
        <main className="flex-1 overflow-y-auto p-4 sm:p-6 lg:p-8">{children}</main>
      </div>
    </div>
  );
}

export default DashboardShell;
