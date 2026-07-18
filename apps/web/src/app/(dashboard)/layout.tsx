import { DashboardShell } from "@/components/layout/dashboard-shell";

/**
 * Authenticated shell shared by every dashboard page: fixed sidebar + topbar
 * + scrollable main. The (auth) group deliberately does NOT inherit this, so
 * the login page renders without the sidebar.
 *
 * (Wave 2 / U2 may add a server-side auth guard here that redirects to /login
 * when the session cookie is absent.)
 */
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return <DashboardShell>{children}</DashboardShell>;
}
