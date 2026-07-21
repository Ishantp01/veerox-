"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { DashboardShell } from "@/components/layout/dashboard-shell";
import { useAuth } from "@/lib/auth-context";

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
 */
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  const router = useRouter();

  useEffect(() => {
    if (status === "unauthenticated") router.replace("/login");
  }, [status, router]);

  if (status !== "authenticated") return null;

  return <DashboardShell>{children}</DashboardShell>;
}
