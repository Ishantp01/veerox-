import { Clock, Loader2, CheckCircle2, XCircle, MinusCircle, Ban, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { FollowUpTaskStatus } from "@/lib/types";

const META: Record<FollowUpTaskStatus, { label: string; cls: string; icon: LucideIcon }> = {
  pending: {
    label: "Pending",
    cls: "bg-slate-100 text-slate-600 ring-slate-200 dark:bg-slate-800 dark:text-slate-400 dark:ring-slate-700",
    icon: Clock,
  },
  sending: {
    label: "Sending",
    cls: "bg-sky-50 text-sky-700 ring-sky-200 dark:bg-sky-500/15 dark:text-sky-400 dark:ring-sky-500/20",
    icon: Loader2,
  },
  sent: {
    label: "Sent",
    cls: "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-400 dark:ring-emerald-500/20",
    icon: CheckCircle2,
  },
  failed: {
    label: "Failed",
    cls: "bg-red-50 text-red-700 ring-red-200 dark:bg-red-500/15 dark:text-red-400 dark:ring-red-500/20",
    icon: XCircle,
  },
  skipped: {
    label: "Skipped",
    cls: "bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-400 dark:ring-amber-500/20",
    icon: MinusCircle,
  },
  cancelled: {
    label: "Cancelled",
    cls: "bg-slate-100 text-slate-500 ring-slate-200 dark:bg-slate-800 dark:text-slate-500 dark:ring-slate-700",
    icon: Ban,
  },
};

export const FOLLOW_UP_TASK_STATUS_OPTIONS: FollowUpTaskStatus[] = [
  "pending",
  "sending",
  "sent",
  "failed",
  "skipped",
  "cancelled",
];

export const FOLLOW_UP_TASK_STATUS_LABELS: Record<FollowUpTaskStatus, string> = {
  pending: "Pending",
  sending: "Sending",
  sent: "Sent",
  failed: "Failed",
  skipped: "Skipped",
  cancelled: "Cancelled",
};

export function FollowUpTaskStatusBadge({ status, className }: { status: FollowUpTaskStatus; className?: string }) {
  const meta = META[status] ?? META.pending;
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
