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
  new: { label: "New", cls: "bg-slate-100 text-slate-600", icon: Circle },
  contacted: { label: "Contacted", cls: "bg-sky-100 text-sky-700", icon: PhoneCall },
  qualified: { label: "Qualified", cls: "bg-indigo-100 text-indigo-700", icon: BadgeCheck },
  converted: { label: "Converted", cls: "bg-emerald-100 text-emerald-700", icon: PartyPopper },
  lost: { label: "Lost", cls: "bg-red-100 text-red-700", icon: XCircle },
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
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap",
        meta.cls,
        className,
      )}
    >
      <Icon size={12} aria-hidden className="shrink-0" />
      {meta.label}
    </span>
  );
}
