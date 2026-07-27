import { CalendarClock, CheckCircle2, PartyPopper, XCircle, UserX, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";
import type { AppointmentStatus } from "@/lib/types";

const META: Record<AppointmentStatus, { label: string; cls: string; icon: LucideIcon }> = {
  scheduled: {
    label: "Scheduled",
    cls: "bg-sky-50 text-sky-700 ring-sky-200 dark:bg-sky-500/15 dark:text-sky-400 dark:ring-sky-500/20",
    icon: CalendarClock,
  },
  confirmed: {
    label: "Confirmed",
    cls: "bg-primary-50 text-primary-700 ring-primary-200 dark:bg-primary-500/15 dark:text-primary-400 dark:ring-primary-500/20",
    icon: CheckCircle2,
  },
  completed: {
    label: "Completed",
    cls: "bg-emerald-50 text-emerald-700 ring-emerald-200 dark:bg-emerald-500/15 dark:text-emerald-400 dark:ring-emerald-500/20",
    icon: PartyPopper,
  },
  cancelled: {
    label: "Cancelled",
    cls: "bg-red-50 text-red-700 ring-red-200 dark:bg-red-500/15 dark:text-red-400 dark:ring-red-500/20",
    icon: XCircle,
  },
  no_show: {
    label: "No-show",
    cls: "bg-slate-100 text-slate-600 ring-slate-200 dark:bg-slate-800 dark:text-slate-400 dark:ring-slate-700",
    icon: UserX,
  },
};

export const APPOINTMENT_STATUS_OPTIONS: AppointmentStatus[] = [
  "scheduled",
  "confirmed",
  "completed",
  "cancelled",
  "no_show",
];

export const APPOINTMENT_STATUS_LABELS: Record<AppointmentStatus, string> = {
  scheduled: "Scheduled",
  confirmed: "Confirmed",
  completed: "Completed",
  cancelled: "Cancelled",
  no_show: "No-show",
};

export function AppointmentStatusBadge({ status, className }: { status: AppointmentStatus; className?: string }) {
  const meta = META[status] ?? META.scheduled;
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
