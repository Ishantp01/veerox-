"use client";

import { usePathname } from "next/navigation";
import { ChevronRight, Menu, ShieldCheck } from "lucide-react";

const SEGMENT_LABELS: Record<string, string> = {
  whatsapp: "WhatsApp",
  calling: "Calling",
  reports: "Reports",
  login: "Login",
  conversations: "Conversations",
  leads: "Leads",
  escalations: "Escalations",
  settings: "Settings",
  send: "Send Message",
  dial: "Dial",
  campaigns: "Campaigns",
  users: "Users",
};

const ID_LIKE = /^[0-9a-f-]{6,}$/i;

function labelFor(segment: string): string {
  if (SEGMENT_LABELS[segment]) return SEGMENT_LABELS[segment];
  if (ID_LIKE.test(segment)) return "Details";
  return segment
    .split("-")
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(" ");
}

export interface TopbarProps {
  /** Opens the mobile nav drawer. Omitted (or the button hidden) at `lg` and up. */
  onMenuClick?: () => void;
}

/**
 * Slim top bar shared by every dashboard page: a breadcrumb derived purely
 * from the URL (no data fetching, so it stays cheap to render everywhere)
 * plus a fixed admin-session indicator. Sits above `{children}` in the
 * (dashboard) layout, alongside the sidebar. Below `lg`, also hosts the
 * hamburger button that opens the off-canvas Nav drawer.
 */
export function Topbar({ onMenuClick }: TopbarProps) {
  const pathname = usePathname();
  const segments = pathname.split("/").filter(Boolean);

  return (
    <header className="sticky top-0 z-20 flex h-16 shrink-0 items-center justify-between gap-3 border-b border-slate-200/80 bg-white/80 px-4 backdrop-blur-md sm:px-6 lg:px-8 dark:border-slate-800/80 dark:bg-slate-950/70">
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          onClick={onMenuClick}
          aria-label="Open navigation"
          className="-ml-1.5 shrink-0 rounded-lg p-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-800 lg:hidden dark:text-slate-400 dark:hover:bg-slate-800 dark:hover:text-slate-100"
        >
          <Menu size={19} aria-hidden />
        </button>
        <nav aria-label="Breadcrumb" className="flex min-w-0 items-center gap-1.5 overflow-x-auto text-sm">
          <span className="shrink-0 font-medium text-slate-400 dark:text-slate-500">Home</span>
          {segments.map((segment, i) => (
            <span key={i} className="flex shrink-0 items-center gap-1.5">
              <ChevronRight size={14} className="text-slate-300 dark:text-slate-700" aria-hidden />
              <span
                className={
                  i === segments.length - 1
                    ? "font-semibold text-slate-800 dark:text-slate-100"
                    : "font-medium text-slate-400 dark:text-slate-500"
                }
              >
                {labelFor(segment)}
              </span>
            </span>
          ))}
        </nav>
      </div>

      <div className="flex shrink-0 items-center gap-2 rounded-full border border-slate-200 bg-white py-1 pl-1 pr-3 text-xs font-semibold text-slate-600 shadow-sm dark:border-slate-800 dark:bg-slate-900 dark:text-slate-300">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-gradient-to-br from-emerald-400 to-emerald-600 text-white">
          <ShieldCheck size={13} aria-hidden />
        </span>
        <span className="hidden sm:inline">Admin session</span>
      </div>
    </header>
  );
}

export default Topbar;
