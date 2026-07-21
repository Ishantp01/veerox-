"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  LayoutDashboard,
  MessageSquare,
  UserCheck,
  AlertTriangle,
  Phone,
  Megaphone,
  Send,
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

type Section = "whatsapp" | "calling";

const SECTIONS: Record<Section, { label: string; root: string; items: NavItem[] }> = {
  whatsapp: {
    label: "WhatsApp",
    root: "/whatsapp",
    items: [
      { href: "/whatsapp", label: "Dashboard", Icon: LayoutDashboard },
      { href: "/whatsapp/conversations", label: "Conversations", Icon: MessageSquare },
      { href: "/whatsapp/leads", label: "Leads", Icon: UserCheck },
      { href: "/whatsapp/escalations", label: "Escalations", Icon: AlertTriangle },
      { href: "/whatsapp/send", label: "Send Message", Icon: Send },
      { href: "/whatsapp/settings", label: "Settings", Icon: Settings },
    ],
  },
  calling: {
    label: "Calling",
    root: "/calling",
    items: [
      { href: "/calling", label: "Dashboard", Icon: LayoutDashboard },
      { href: "/calling/conversations", label: "Conversations", Icon: MessageSquare },
      { href: "/calling/leads", label: "Leads", Icon: UserCheck },
      { href: "/calling/escalations", label: "Escalations", Icon: AlertTriangle },
      { href: "/calling/dial", label: "Dial", Icon: Phone },
      { href: "/calling/campaigns", label: "Campaigns", Icon: Megaphone },
      { href: "/calling/settings", label: "Settings", Icon: Settings },
    ],
  },
};

// Section-root hrefs (e.g. "/whatsapp") must match exactly, not by prefix —
// otherwise the Dashboard row would stay "active" on every sub-route too
// (e.g. /whatsapp/conversations starts with /whatsapp).
function isActive(pathname: string, href: string, sectionRoot: string): boolean {
  if (href === sectionRoot) return pathname === href;
  return pathname.startsWith(href);
}

export function activeSectionFor(pathname: string): Section {
  return pathname.startsWith("/calling") ? "calling" : "whatsapp";
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
  const router = useRouter();
  const { isAuthenticated, logout } = useAuth();
  const activeSection = activeSectionFor(pathname);
  const section = SECTIONS[activeSection];

  // Close the mobile drawer whenever the route changes (link tap or the
  // section-switcher's router.push) instead of requiring a second tap.
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
        className={`fixed inset-y-0 left-0 z-50 flex h-screen w-72 max-w-[85vw] flex-col overflow-y-auto border-r border-white/[0.06] bg-canvas-950 bg-sidebar-fade px-3 py-6 shrink-0 transition-transform duration-200 ease-out lg:sticky lg:top-0 lg:z-0 lg:w-64 lg:max-w-none lg:translate-x-0 ${
          mobileOpen ? "translate-x-0 shadow-2xl" : "-translate-x-full"
        }`}
      >
        {/* Logo */}
        <div className="mb-7 px-3 flex items-center justify-between gap-2.5">
          <div className="flex items-center gap-2.5">
            <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-gradient-to-br from-primary-400 to-primary-600 text-white shrink-0 shadow-glow">
              <Sparkles size={16} strokeWidth={2.25} />
            </div>
            <div>
              <p className="text-sm font-semibold tracking-tight text-white leading-none">Veerox AI</p>
              <p className="text-[10px] font-medium text-slate-500 mt-1.5 uppercase tracking-widest">Admin Panel</p>
            </div>
          </div>
          <button
            type="button"
            onClick={onCloseMobile}
            aria-label="Close navigation"
            className="rounded-lg p-1.5 text-slate-500 hover:bg-white/[0.06] hover:text-slate-200 lg:hidden"
          >
            <X size={18} aria-hidden />
          </button>
        </div>

      {/* Product switcher — selects which agent's nav items are shown below,
          avoiding two full duplicated lists rendered at once. */}
      <div
        role="tablist"
        aria-label="Agent channel"
        className="mb-7 grid grid-cols-2 gap-0.5 rounded-xl bg-white/[0.04] p-1 ring-1 ring-white/[0.06]"
      >
        {(Object.keys(SECTIONS) as Section[]).map((key) => {
          const isSelected = key === activeSection;
          return (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={isSelected}
              onClick={() => router.push(SECTIONS[key].root)}
              className={`rounded-lg px-2 py-1.5 text-xs font-semibold transition-all duration-150 ${
                isSelected
                  ? "bg-gradient-to-b from-primary-500 to-primary-600 text-white shadow-glow"
                  : "text-slate-500 hover:text-slate-200"
              }`}
            >
              {SECTIONS[key].label}
            </button>
          );
        })}
      </div>

      {/* Nav items for the active section */}
      <div className="flex flex-1 flex-col">
        <p className="px-3 mb-2 text-[10px] font-bold uppercase tracking-widest text-slate-600">
          {section.label} AI Agent
        </p>
        <ul className="flex flex-col gap-0.5">
          {section.items.map(({ href, label, Icon }) => {
            const active = isActive(pathname, href, section.root);
            return (
              <li key={href}>
                <Link
                  href={href}
                  className={`relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-all duration-150 ${
                    active
                      ? "bg-white/[0.08] text-white"
                      : "text-slate-500 hover:bg-white/[0.04] hover:text-slate-200"
                  }`}
                >
                  {active && (
                    <span className="absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full bg-gradient-to-b from-primary-300 to-primary-500 shadow-glow" />
                  )}
                  <Icon size={16} strokeWidth={2} className={`shrink-0 ${active ? "text-primary-400" : ""}`} />
                  {label}
                </Link>
              </li>
            );
          })}
        </ul>
      </div>

      {/* Login/Logout (outside the channel switcher) */}
      {isAuthenticated ? (
        <button
          type="button"
          onClick={logout}
          className="flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium text-slate-500 transition-colors duration-150 hover:bg-white/[0.04] hover:text-slate-200"
        >
          <LogOut size={16} className="shrink-0" />
          Logout
        </button>
      ) : (
        <Link
          href="/login"
          className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors duration-150 ${
            pathname === "/login"
              ? "bg-white/[0.08] text-white"
              : "text-slate-500 hover:bg-white/[0.04] hover:text-slate-200"
          }`}
        >
          <LogIn size={16} className="shrink-0" />
          Login
        </Link>
      )}

      {/* Footer */}
      <div className="px-3 py-3 mt-2 border-t border-white/[0.06] flex items-center gap-2.5">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-slate-700 to-slate-800 text-[11px] font-bold text-slate-300 ring-1 ring-white/10">
          VX
        </div>
        <div>
          <p className="text-xs font-medium text-slate-400">v0.1.0 · Dev Mode</p>
          <p className="text-[11px] text-slate-600">Voice + WhatsApp Agent</p>
        </div>
      </div>
      </nav>
    </>
  );
}
