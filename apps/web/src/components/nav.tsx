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
    <nav className="flex h-screen w-60 flex-col overflow-y-auto border-r border-white/10 bg-canvas-950 px-3 py-6 shrink-0">
      {/* Logo */}
      <div className="mb-6 px-3 flex items-center gap-2.5">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-primary-600 text-white shrink-0">
          <Sparkles size={15} strokeWidth={2.25} />
        </div>
        <div>
          <p className="text-sm font-semibold tracking-tight text-white leading-none">Veerox AI</p>
          <p className="text-[10px] font-medium text-slate-500 mt-1 uppercase tracking-widest">Admin Panel</p>
        </div>
      </div>

      {/* Product switcher — selects which agent's nav items are shown below,
          avoiding two full duplicated lists rendered at once. */}
      <div
        role="tablist"
        aria-label="Agent channel"
        className="mb-6 grid grid-cols-2 gap-0.5 rounded-lg bg-white/5 p-1"
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
              className={`rounded-md px-2 py-1.5 text-xs font-semibold transition-colors duration-150 ${
                isSelected
                  ? "bg-white/10 text-white"
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
        <p className="px-3 mb-2 text-[10px] font-semibold uppercase tracking-widest text-slate-600">
          {section.label} AI Agent
        </p>
        <ul className="flex flex-col gap-0.5">
          {section.items.map(({ href, label, Icon }) => {
            const active = isActive(pathname, href, section.root);
            return (
              <li key={href}>
                <Link
                  href={href}
                  className={`relative flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors duration-150 ${
                    active
                      ? "bg-white/[0.07] text-white"
                      : "text-slate-500 hover:bg-white/[0.04] hover:text-slate-200"
                  }`}
                >
                  {active && (
                    <span className="absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 rounded-full bg-primary-400" />
                  )}
                  <Icon size={16} strokeWidth={2} className="shrink-0" />
                  {label}
                </Link>
              </li>
            );
          })}
        </ul>
      </div>

      {/* Login (outside the channel switcher) */}
      <Link
        href="/login"
        className={`flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors duration-150 ${
          pathname === "/login"
            ? "bg-white/[0.07] text-white"
            : "text-slate-500 hover:bg-white/[0.04] hover:text-slate-200"
        }`}
      >
        <LogIn size={16} className="shrink-0" />
        Login
      </Link>

      {/* Footer */}
      <div className="px-3 py-3 mt-2 border-t border-white/10">
        <p className="text-xs font-medium text-slate-500">v0.1.0 · Dev Mode</p>
        <p className="text-[11px] text-slate-600 mt-0.5">Voice + WhatsApp Agent</p>
      </div>
    </nav>
  );
}
