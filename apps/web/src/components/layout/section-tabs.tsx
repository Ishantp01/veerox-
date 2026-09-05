"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { LucideIcon } from "lucide-react";

export interface SectionTabItem {
  href: string;
  label: string;
  Icon: LucideIcon;
}

// Exact match on the section root (otherwise every sub-route would also
// match the root tab by prefix); prefix match everywhere else.
function isTabActive(pathname: string, href: string, sectionRoot: string): boolean {
  if (href === sectionRoot) return pathname === href;
  return pathname === href || pathname.startsWith(`${href}/`);
}

/**
 * Secondary in-section navigation (Conversations / Leads / Escalations / ...)
 * for a channel whose top-level sidebar entry (AI Calling, AI WhatsApp) now
 * links to a single root rather than listing each sub-page in the primary
 * sidebar. Rendered from each channel's `layout.tsx` above `{children}`.
 */
export function SectionTabs({ items, sectionRoot }: { items: SectionTabItem[]; sectionRoot: string }) {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Section navigation"
      data-tour="section-tabs"
      className="-mx-4 mb-6 flex gap-1 overflow-x-auto border-b border-slate-200 px-4 sm:-mx-6 sm:px-6 lg:-mx-8 lg:px-8 dark:border-slate-800"
    >
      {items.map(({ href, label, Icon }) => {
        const active = isTabActive(pathname, href, sectionRoot);
        return (
          <Link
            key={href}
            href={href}
            className={`relative flex shrink-0 items-center gap-2 px-3 py-2.5 text-sm font-medium transition-colors duration-150 ${
              active
                ? "text-primary-600 dark:text-primary-400"
                : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-100"
            }`}
          >
            <Icon size={15} strokeWidth={2} className="shrink-0" />
            {label}
            {active && (
              <span className="absolute inset-x-3 -bottom-px h-0.5 rounded-full bg-primary-500" />
            )}
          </Link>
        );
      })}
    </nav>
  );
}

export default SectionTabs;
