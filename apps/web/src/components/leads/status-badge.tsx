import {
  Circle,
  PhoneCall,
  BadgeCheck,
  PartyPopper,
  XCircle,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import type { LeadStatus } from "@/lib/types";

/**
 * Visual treatment per lead status. Color is never the sole signal — each
 * status pairs a color bundle with a Lucide icon (a11y, UI plan §10).
 */
const STATUS_META: Record<LeadStatus, { label: string; cls: string; icon: LucideIcon }> = {
  new: { label: "New", cls: "bg-slate-100 text-slate-600 ring-slate-200 dark:bg-slate-800 dark:text-slate-400 dark:ring-slate-700", icon: Circle },
  contacted: { label: "Contacted", cls: "bg-sky-50 text-sky-700 ring-sky-200 dark:bg-sky-500/15 dark:text-sky-400 dark:ring-sky-500/20", icon: PhoneCall },
  qualified: { label: "Qualified", cls: "bg-primary-50 text-primary-700 ring-primary-200 dark:bg-primary-500/15 dark:text-primary-400 dark:ring-primary-500/20", icon: BadgeCheck },
  converted: { label: "Converted", cls: "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-400 dark:ring-emerald-500/20", icon: PartyPopper },
  lost: { label: "Lost", cls: "bg-red-50 text-red-700 ring-red-200 dark:bg-red-500/15 dark:text-red-400 dark:ring-red-500/20", icon: XCircle },
};

export const LEAD_STATUS_OPTIONS: LeadStatus[] = [
  "new",
  "contacted",
  "qualified",
  "converted",
  "lost",
];

/** Human-readable label per status — for <select> options, which can't render icons/color. */
export const LEAD_STATUS_LABELS: Record<LeadStatus, string> = {
  new: "New",
  contacted: "Contacted",
  qualified: "Qualified",
  converted: "Converted",
  lost: "Lost",
};

export interface StatusBadgeProps {
  status: LeadStatus;
  className?: string;
}

/** Pill describing a lead's CRM stage. */
export function StatusBadge({ status, className }: StatusBadgeProps) {
  const meta = STATUS_META[status] ?? STATUS_META.new;
  const Icon = meta.icon;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-semibold whitespace-nowrap ring-1 ring-inset",
        meta.cls,
        className,
      )}
    >
      <Icon size={12} aria-hidden className="shrink-0" />
      {meta.label}
    </span>
  );
}
