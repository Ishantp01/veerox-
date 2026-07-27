import { Circle, Search, BadgeCheck, XCircle, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { LeadQualificationStatus } from "@/lib/types";

const QUALIFICATION_META: Record<LeadQualificationStatus, { label: string; cls: string; icon: LucideIcon }> = {
  unqualified: {
    label: "Unqualified",
    cls: "bg-slate-100 text-slate-600 ring-slate-200 dark:bg-slate-800 dark:text-slate-400 dark:ring-slate-700",
    icon: Circle,
  },
  in_review: {
    label: "In Review",
    cls: "bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-400 dark:ring-amber-500/20",
    icon: Search,
  },
  qualified: {
    label: "Qualified",
    cls: "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-400 dark:ring-emerald-500/20",
    icon: BadgeCheck,
  },
  disqualified: {
    label: "Disqualified",
    cls: "bg-red-50 text-red-700 ring-red-200 dark:bg-red-500/15 dark:text-red-400 dark:ring-red-500/20",
    icon: XCircle,
  },
};

export const LEAD_QUALIFICATION_OPTIONS: LeadQualificationStatus[] = [
  "unqualified",
  "in_review",
  "qualified",
  "disqualified",
];

export const LEAD_QUALIFICATION_LABELS: Record<LeadQualificationStatus, string> = {
  unqualified: "Unqualified",
  in_review: "In Review",
  qualified: "Qualified",
  disqualified: "Disqualified",
};

export interface QualificationBadgeProps {
  status: LeadQualificationStatus;
  className?: string;
}

/** Pill describing a lead's qualification-pipeline stage (distinct from StatusBadge's CRM stage). */
export function QualificationBadge({ status, className }: QualificationBadgeProps) {
  const meta = QUALIFICATION_META[status] ?? QUALIFICATION_META.unqualified;
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
