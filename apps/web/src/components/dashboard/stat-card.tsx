import { type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export type StatTint = "primary" | "sky" | "emerald" | "rose" | "amber";

const TINT_CLASSES: Record<StatTint, { chip: string; bar: string }> = {
  primary: { chip: "from-primary-400 to-primary-600", bar: "from-primary-400 to-primary-600" },
  sky: { chip: "from-sky-400 to-sky-600", bar: "from-sky-400 to-sky-600" },
  emerald: { chip: "from-emerald-400 to-emerald-600", bar: "from-emerald-400 to-emerald-600" },
  rose: { chip: "from-rose-400 to-rose-600", bar: "from-rose-400 to-rose-600" },
  amber: { chip: "from-amber-400 to-amber-600", bar: "from-amber-400 to-amber-600" },
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
  const { chip, bar } = TINT_CLASSES[tint];
  return (
    <div
      className={cn(
        "group relative overflow-hidden rounded-2xl border border-slate-200/80 bg-white p-5 shadow-card transition-all duration-200 hover:-translate-y-0.5 hover:shadow-card-lg dark:border-slate-800 dark:bg-slate-900",
        className,
      )}
    >
      <div className={cn("absolute inset-x-0 top-0 h-1 bg-gradient-to-r opacity-70 transition-opacity duration-200 group-hover:opacity-100", bar)} />
      <div className="flex items-start justify-between gap-3">
        <p className="text-xs font-semibold text-slate-500 dark:text-slate-400">{label}</p>
        {Icon && (
          <div className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br text-white shadow-sm", chip)}>
            <Icon size={16} aria-hidden />
          </div>
        )}
      </div>
      <p className="mt-3.5 text-[1.75rem] font-bold tabular-nums leading-none tracking-tight text-slate-900 dark:text-slate-50">
        {value}
      </p>
      {sublabel && <p className="mt-2.5 text-xs text-slate-400 dark:text-slate-500">{sublabel}</p>}
    </div>
  );
}

export default StatCard;
