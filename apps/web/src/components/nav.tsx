"use client";

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
  Sparkles,
  type LucideIcon,
} from "lucide-react";

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

export default function Nav() {
  const pathname = usePathname();
  const router = useRouter();
  const activeSection = activeSectionFor(pathname);
  const section = SECTIONS[activeSection];

  return (
    <nav className="flex h-screen w-60 flex-col overflow-y-auto border-r border-slate-800 bg-gradient-to-b from-slate-900 to-slate-950 px-3 py-6 shrink-0">
      {/* Logo */}
      <div className="mb-6 px-3 flex items-center gap-3">
        <div className="relative flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-primary-400 via-primary-600 to-violet-700 text-white shadow-elevated shadow-primary-950/50 shrink-0">
          <Sparkles size={17} strokeWidth={2.25} />
        </div>
        <div>
          <p className="text-sm font-bold tracking-tight text-white leading-none">Veerox AI</p>
          <p className="text-[10px] font-semibold text-primary-400 mt-0.5 uppercase tracking-widest">Admin Panel</p>
        </div>
      </div>

      {/* Product switcher — selects which agent's nav items are shown below,
          avoiding two full duplicated lists rendered at once. */}
      <div
        role="tablist"
        aria-label="Agent channel"
        className="mb-6 grid grid-cols-2 gap-0.5 rounded-xl bg-slate-800/70 p-1"
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
              className={`rounded-lg px-2 py-1.5 text-xs font-bold transition-all duration-150 ${
                isSelected
                  ? "bg-gradient-to-b from-primary-500 to-primary-600 text-white shadow-md shadow-primary-950/40"
                  : "text-slate-400 hover:text-slate-100"
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
                  className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-all duration-150 ${
                    active
                      ? "bg-gradient-to-b from-primary-500 to-primary-600 text-white shadow-lg shadow-primary-950/40"
                      : "text-slate-400 hover:bg-slate-800 hover:text-slate-100"
                  }`}
                >
                  <Icon size={16} strokeWidth={active ? 2.5 : 2} className="shrink-0" />
                  {label}
                  {active && <span className="ml-auto h-1.5 w-1.5 rounded-full bg-white/50" />}
                </Link>
              </li>
            );
          })}
        </ul>
      </div>

      {/* Login (outside the channel switcher) */}
      <Link
        href="/login"
        className={`flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm font-medium transition-all duration-150 ${
          pathname === "/login"
            ? "bg-gradient-to-b from-primary-500 to-primary-600 text-white shadow-lg shadow-primary-950/40"
            : "text-slate-400 hover:bg-slate-800 hover:text-slate-100"
        }`}
      >
        <LogIn size={16} className="shrink-0" />
        Login
      </Link>

      {/* Footer */}
      <div className="rounded-xl bg-slate-800/70 px-3 py-3 mt-2 border border-slate-700/50">
        <p className="text-xs font-semibold text-slate-400">v0.1.0 · Dev Mode</p>
        <p className="text-[11px] text-slate-600 mt-0.5">Voice + WhatsApp Agent</p>
      </div>
    </nav>
  );
}
