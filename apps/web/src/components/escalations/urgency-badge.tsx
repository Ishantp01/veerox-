import {
  AlertTriangle,
  ArrowDown,
  ArrowUpCircle,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

type UrgencyLevel = "high" | "medium" | "low";

const URGENCY_META: Record<
  UrgencyLevel,
  { label: string; cls: string; icon: LucideIcon }
> = {
  high: { label: "High", cls: "bg-red-50 text-red-700 ring-red-200 dark:bg-red-500/15 dark:text-red-400 dark:ring-red-500/20", icon: AlertTriangle },
  medium: { label: "Medium", cls: "bg-amber-50 text-amber-700 ring-amber-200 dark:bg-amber-500/15 dark:text-amber-400 dark:ring-amber-500/20", icon: ArrowUpCircle },
  low: { label: "Low", cls: "bg-slate-100 text-slate-600 ring-slate-200 dark:bg-slate-800 dark:text-slate-400 dark:ring-slate-700", icon: ArrowDown },
};

/** Normalize the backend's free-form urgency string to one of three levels. */
function normalize(urgency: string | null | undefined): UrgencyLevel {
  const u = (urgency ?? "").toLowerCase();
  if (u === "high" || u === "urgent" || u === "critical") return "high";
  if (u === "low") return "low";
  return "medium";
}

export interface UrgencyBadgeProps {
  urgency: string | null | undefined;
  className?: string;
}

/** Pill describing how urgent a human handoff is. Color + icon (a11y §10). */
export function UrgencyBadge({ urgency, className }: UrgencyBadgeProps) {
  const meta = URGENCY_META[normalize(urgency)];
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
