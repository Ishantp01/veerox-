import Nav from "@/components/nav";
import { Topbar } from "@/components/layout/topbar";

/**
 * Authenticated shell shared by every dashboard page: fixed sidebar + topbar
 * + scrollable main. The (auth) group deliberately does NOT inherit this, so
 * the login page renders without the sidebar.
 *
 * (Wave 2 / U2 may add a server-side auth guard here that redirects to /login
 * when the session cookie is absent.)
 */
export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen overflow-hidden">
      <Nav />
      <div className="flex flex-1 flex-col overflow-hidden">
        <Topbar />
        <main className="flex-1 overflow-y-auto p-8">{children}</main>
      </div>
    </div>
  );
}
