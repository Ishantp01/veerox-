"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  MessageSquare,
  MessagesSquare,
  FileText,
  Phone,
  Users,
  UserCheck,
  BadgeCheck,
  CalendarClock,
  AlertTriangle,
  BarChart3,
  TrendingUp,
  Megaphone,
  Repeat,
  Settings,
  LogIn,
  LogOut,
  Sparkles,
  X,
  type LucideIcon,
} from "lucide-react";
import { useAuth } from "@/lib/auth-context";

interface NavItem {
  href: string;
  label: string;
  Icon: LucideIcon;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

// One persistent sidebar grouped by function (not by channel) — replaces the
// old WhatsApp/Calling tab switcher. AI Calling / AI WhatsApp each link to
// their channel root; the conversations/leads/escalations/settings pages that
// used to be flat top-level items now live as secondary nav within each
// channel section (see components/layout/section-tabs.tsx).
const GROUPS: NavGroup[] = [
  {
    label: "Main",
    items: [{ href: "/", label: "Dashboard", Icon: LayoutDashboard }],
  },
  {
    label: "Communication",
    items: [
      { href: "/calling", label: "AI Calling", Icon: Phone },
      { href: "/whatsapp", label: "AI WhatsApp", Icon: MessageSquare },
      { href: "/whatsapp/templates", label: "WhatsApp Templates", Icon: FileText },
    ],
  },
  {
    label: "CRM",
    items: [
      { href: "/crm/contacts", label: "Contacts", Icon: Users },
      { href: "/crm/leads", label: "Leads", Icon: UserCheck },
      { href: "/crm/qualification", label: "Lead Qualification", Icon: BadgeCheck },
      { href: "/crm/appointments", label: "Appointments", Icon: CalendarClock },
      { href: "/conversations", label: "Conversations", Icon: MessagesSquare },
      { href: "/escalations", label: "Escalations", Icon: AlertTriangle },
    ],
  },
  {
    label: "Analytics",
    items: [
      { href: "/reports", label: "Reports", Icon: BarChart3 },
      { href: "/analytics/sales", label: "Sales Dashboard", Icon: TrendingUp },
    ],
  },
  {
    label: "Automation",
    items: [
      { href: "/automation/campaigns", label: "Campaigns", Icon: Megaphone },
      { href: "/automation/follow-ups", label: "Automated Follow-up", Icon: Repeat },
    ],
  },
  {
    label: "Settings",
    items: [{ href: "/settings", label: "Settings", Icon: Settings }],
  },
];

const ALL_HREFS = GROUPS.flatMap((group) => group.items.map((item) => item.href));

// Exact match on a group root ("/", "/calling", "/whatsapp", ...) — otherwise
// every sub-route would also match its section's own root by prefix. A root
// is anything another nav item's href is nested under (e.g. "/whatsapp" vs.
// "/whatsapp/templates"). Prefix match everywhere else so e.g.
// "/crm/leads/123" keeps "Leads" highlighted.
function isActive(pathname: string, href: string): boolean {
  const isRoot = href === "/" || ALL_HREFS.some((other) => other !== href && other.startsWith(`${href}/`));
  if (isRoot) return pathname === href;
  return pathname === href || pathname.startsWith(`${href}/`);
}

export interface NavProps {
  /** Mobile/tablet drawer open state (ignored at the `lg` breakpoint and up,
   * where the sidebar is always visible statically). */
  mobileOpen?: boolean;
  /** Called to dismiss the mobile drawer (backdrop click, Escape, nav link tap). */
  onCloseMobile?: () => void;
}

export default function Nav({ mobileOpen = false, onCloseMobile }: NavProps) {
  const pathname = usePathname();
  const { isAuthenticated, logout } = useAuth();

  // Close the mobile drawer whenever the route changes instead of requiring
  // a second tap.
  useEffect(() => {
    onCloseMobile?.();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);

  return (
    <>
      {/* Backdrop — mobile/tablet only, sits below the drawer and above page content. */}
      {mobileOpen && (
        <div
          aria-hidden
          onClick={onCloseMobile}
          className="fixed inset-0 z-40 bg-slate-900/60 backdrop-blur-sm lg:hidden"
        />
      )}
      <nav
        className={`fixed inset-y-0 left-0 z-50 flex h-screen w-72 max-w-[85vw] flex-col overflow-y-auto border-r border-slate-200 bg-white px-3 py-6 shrink-0 transition-transform duration-200 ease-out lg:sticky lg:top-0 lg:z-0 lg:w-64 lg:max-w-none lg:translate-x-0 dark:border-white/[0.06] dark:bg-canvas-950 dark:bg-sidebar-fade ${
          mobileOpen ? "translate-x-0 shadow-2xl" : "-translate-x-full"
        }`}
      >
        {/* Logo */}
        <div className="mb-6 px-3 flex items-center justify-between gap-2.5">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-primary-400 to-primary-600 text-white shrink-0 shadow-glow">
              <Sparkles size={16} strokeWidth={2.25} />
            </div>
            <div>
              <p className="text-sm font-semibold tracking-tight leading-none text-slate-900 dark:text-white">Veerox AI</p>
              <p className="text-[10px] font-medium text-slate-400 mt-1.5 uppercase tracking-widest dark:text-slate-500">Admin Panel</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onCloseMobile}
            aria-label="Close navigation"
            className="rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700 lg:hidden dark:text-slate-500 dark:hover:bg-white/[0.06] dark:hover:text-slate-200"
          >
            <X size={18} aria-hidden />
          </button>
        </div>

        {/* Grouped nav */}
        <div className="flex flex-1 flex-col gap-5">
          {GROUPS.map((group) => (
            <div key={group.label}>
              <p className="px-3 mb-1.5 text-[10px] font-bold uppercase tracking-widest text-slate-400 dark:text-slate-600">
                {group.label}
              </p>
              <ul className="flex flex-col gap-0.5">
                {group.items.map(({ href, label, Icon }) => {
                  const active = isActive(pathname, href);
                  return (
                    <li key={href}>
                      <Link
                        href={href}
                        className={`relative flex items-center gap-3 rounded-lg border-l-[3px] px-3 py-2 text-sm font-medium transition-all duration-150 ${
                          active
                            ? "border-primary-500 bg-primary-50 text-slate-900 dark:bg-[#000e1d] dark:text-white"
                            : "border-transparent text-slate-500 hover:bg-slate-100 hover:text-slate-800 dark:text-slate-500 dark:hover:bg-white/[0.04] dark:hover:text-slate-200"
                        }`}
                      >
                        <Icon
                          size={16}
                          strokeWidth={2}
                          className={`shrink-0 ${active ? "text-primary-600 dark:text-primary-400" : ""}`}
                        />
                        {label}
                      </Link>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>

        {/* Login/Logout */}
        {isAuthenticated ? (
          <button
            type="button"
            onClick={logout}
            className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-slate-500 transition-colors duration-150 hover:bg-slate-100 hover:text-slate-800 dark:hover:bg-white/[0.04] dark:hover:text-slate-200"
          >
            <LogOut size={16} className="shrink-0" />
            Logout
          </button>
        ) : (
          <Link
            href="/login"
            className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors duration-150 ${
              pathname === "/login"
                ? "bg-primary-50 text-slate-900 dark:bg-white/[0.08] dark:text-white"
                : "text-slate-500 hover:bg-slate-100 hover:text-slate-800 dark:hover:bg-white/[0.04] dark:hover:text-slate-200"
            }`}
          >
            <LogIn size={16} className="shrink-0" />
            Login
          </Link>
        )}

        {/* Footer */}
        <div className="px-3 py-3 mt-2 border-t border-slate-200 flex items-center gap-2.5 dark:border-white/[0.06]">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-slate-300 to-slate-400 text-[11px] font-bold text-slate-700 ring-1 ring-slate-200 dark:from-slate-700 dark:to-slate-800 dark:text-slate-300 dark:ring-white/10">
            VX
          </div>
          <div>
            <p className="text-xs font-medium text-slate-500 dark:text-slate-400">v0.1.0 · Dev Mode</p>
            <p className="text-[11px] text-slate-400 dark:text-slate-600">Voice + WhatsApp Agent</p>
          </div>
        </div>
      </nav>
    </>
  );
}
