import { type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export type StatTint = "primary" | "sky" | "emerald" | "rose" | "amber";

const TINT_CLASSES: Record<StatTint, { chip: string }> = {
  primary: { chip: "bg-primary-50 text-primary-600" },
  sky: { chip: "bg-sky-50 text-sky-600" },
  emerald: { chip: "bg-emerald-50 text-emerald-600" },
  rose: { chip: "bg-rose-50 text-rose-600" },
  amber: { chip: "bg-amber-50 text-amber-600" },
};

export interface StatCardProps {
  label: string;
  value: string | number;
  sublabel?: string;
  /** Optional Lucide icon shown in a colored chip. */
  icon?: LucideIcon;
  /** Color family driving both the icon chip and the top accent bar. */
  tint?: StatTint;
  className?: string;
}

/**
 * Single headline metric for the dashboard. A colored icon chip + confident
 * number weight give it real presence instead of reading as a bare label +
 * number in a box.
 */
export function StatCard({
  label,
  value,
  sublabel,
  icon: Icon,
  tint = "primary",
  className,
}: StatCardProps) {
  const { chip } = TINT_CLASSES[tint];
  return (
    <div
      className={cn(
        "rounded-xl border border-slate-200 bg-white p-5 shadow-elevated transition-colors duration-150 hover:border-slate-300",
        className,
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs font-medium text-slate-500">{label}</p>
        {Icon && (
          <div className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg", chip)}>
            <Icon size={15} aria-hidden />
          </div>
        )}
      </div>
      <p className="mt-3 text-2xl font-semibold tabular-nums tracking-tight text-slate-900">
        {value}
      </p>
      {sublabel && <p className="mt-1.5 text-xs text-slate-400">{sublabel}</p>}
    </div>
  );
}

export default StatCard;
