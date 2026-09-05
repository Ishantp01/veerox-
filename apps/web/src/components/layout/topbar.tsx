"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import { Building2, ChevronRight, Compass, HelpCircle, Menu, Moon, Sun } from "lucide-react";
import { useAuth } from "@/lib/auth-context";
import { useWhatsAppSettings } from "@/lib/hooks";
import { useTour } from "@/components/onboarding/onboarding-tour";

// Social links icon row (SocialLinksRow, useSocialLinks, SOCIAL_META) was
// removed from this topbar — see removefeature.md to re-add.

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

  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const isDark = mounted && resolvedTheme === "dark";

  const whatsapp = useWhatsAppSettings();
  const connected = whatsapp.data?.configured ?? false;
  const { user } = useAuth();
  const { startTour, startPageGuide, hasPageGuide } = useTour();

  return (
    <header className="sticky top-0 z-20 flex h-16 shrink-0 items-center justify-between gap-3 border-b border-slate-200/80 bg-white/80 px-4 backdrop-blur-md sm:px-6 lg:px-8 dark:border-slate-700/80 dark:bg-slate-950/70">
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

      <div className="flex shrink-0 items-center gap-3">
        {/* <SocialLinksRow /> was here — see removefeature.md to re-add. */}
        {user && (
          <div
            className="hidden items-center gap-2 rounded-full border border-slate-200 bg-slate-100 px-3 py-1 md:flex dark:border-slate-700 dark:bg-slate-900"
            title={`Org ID: ${user.org_id}`}
          >
            <Building2 size={12} className="text-slate-400 dark:text-slate-500" aria-hidden />
            <span className="max-w-[10rem] truncate text-[11px] font-medium text-slate-600 dark:text-slate-300">
              {user.org_name}
            </span>
          </div>
        )}
        {!whatsapp.isLoading && (
          <div
            className={`hidden items-center gap-2 rounded-full border px-3 py-1 md:flex ${
              connected
                ? "border-green-500/20 bg-green-500/10"
                : "border-slate-300 bg-slate-100 dark:border-slate-700 dark:bg-slate-900"
            }`}
          >
            <span
              className={`h-2 w-2 rounded-full ${connected ? "bg-green-500 animate-pulse" : "bg-slate-400 dark:bg-slate-600"}`}
              aria-hidden
            />
            <span className={`text-[11px] font-medium ${connected ? "text-green-500" : "text-slate-500 dark:text-slate-400"}`}>
              WhatsApp {connected ? "Connected" : "Not connected"}
            </span>
          </div>
        )}
        {hasPageGuide && (
          <button
            type="button"
            onClick={() => startPageGuide()}
            aria-label="How to use this page"
            title="How to use this page"
            className="flex h-7 items-center gap-1.5 rounded-full border border-slate-200 bg-slate-100 px-3 text-[11px] font-medium text-slate-600 transition-colors hover:bg-slate-200 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
          >
            <HelpCircle size={12} aria-hidden />
            <span className="hidden sm:inline">This page</span>
          </button>
        )}
        <button
          type="button"
          onClick={startTour}
          aria-label="Take the full product tour"
          title="Take the full product tour"
          className="flex h-7 items-center gap-1.5 rounded-full border border-slate-200 bg-slate-100 px-3 text-[11px] font-medium text-slate-600 transition-colors hover:bg-slate-200 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-300 dark:hover:bg-slate-700"
        >
          <Compass size={12} aria-hidden />
          <span className="hidden sm:inline">Tour</span>
        </button>
        <button
          type="button"
          role="switch"
          aria-checked={isDark}
          onClick={() => setTheme(isDark ? "light" : "dark")}
          aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
          className="relative flex h-7 w-14 shrink-0 items-center rounded-full border border-slate-200 bg-slate-100 px-1 shadow-inner transition-colors dark:border-slate-700 dark:bg-slate-800"
        >
          <Sun size={12} className="absolute left-1.5 text-slate-400" aria-hidden />
          <Moon size={12} className="absolute right-1.5 text-slate-500 dark:text-slate-400" aria-hidden />
          <span
            className={`relative z-10 flex h-5 w-5 items-center justify-center rounded-full bg-white shadow-sm transition-transform duration-200 dark:bg-primary-500 ${
              isDark ? "translate-x-7" : "translate-x-0"
            }`}
          />
        </button>
      </div>
    </header>
  );
}

export default Topbar;
