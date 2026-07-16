"use client";

import { usePathname } from "next/navigation";
import { ChevronRight, ShieldCheck } from "lucide-react";

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

/**
 * Slim top bar shared by every dashboard page: a breadcrumb derived purely
 * from the URL (no data fetching, so it stays cheap to render everywhere)
 * plus a fixed admin-session indicator. Sits above `{children}` in the
 * (dashboard) layout, alongside the sidebar.
 */
export function Topbar() {
  const pathname = usePathname();
  const segments = pathname.split("/").filter(Boolean);

  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-slate-200 bg-white px-8">
      <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-sm">
        <span className="font-medium text-slate-400">Home</span>
        {segments.map((segment, i) => (
          <span key={i} className="flex items-center gap-1.5">
            <ChevronRight size={14} className="text-slate-300" aria-hidden />
            <span
              className={
                i === segments.length - 1
                  ? "font-semibold text-slate-800"
                  : "font-medium text-slate-400"
              }
            >
              {labelFor(segment)}
            </span>
          </span>
        ))}
      </nav>

      <div className="flex items-center gap-2 rounded-full border border-slate-200 py-1 pl-1 pr-3 text-xs font-medium text-slate-500">
        <span className="flex h-6 w-6 items-center justify-center rounded-full bg-primary-50 text-primary-600">
          <ShieldCheck size={13} aria-hidden />
        </span>
        Admin session
      </div>
    </header>
  );
}

export default Topbar;
