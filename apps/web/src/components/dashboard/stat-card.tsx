import { type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export type StatTint = "primary" | "sky" | "emerald" | "rose" | "amber" | "purple" | "red";

// Flat, translucent icon tile (tinted background + tinted icon, no gradient
// chip) — matches the reference dashboard's stat-icon treatment exactly
// (e.g. `background: rgba(59,130,246,.15); color: #60A5FA`).
const TINT_CLASSES: Record<StatTint, { iconBg: string; iconColor: string; bar: string }> = {
  primary: { iconBg: "bg-primary-500/15", iconColor: "text-primary-400", bar: "from-primary-400 to-primary-600" },
  sky: { iconBg: "bg-sky-500/15", iconColor: "text-sky-400", bar: "from-sky-400 to-sky-600" },
  emerald: { iconBg: "bg-green-500/15", iconColor: "text-green-400", bar: "from-green-400 to-green-600" },
  rose: { iconBg: "bg-rose-500/15", iconColor: "text-rose-400", bar: "from-rose-400 to-rose-600" },
  amber: { iconBg: "bg-amber-500/15", iconColor: "text-amber-400", bar: "from-amber-400 to-amber-600" },
  purple: { iconBg: "bg-purple-500/15", iconColor: "text-purple-400", bar: "from-purple-400 to-purple-600" },
  red: { iconBg: "bg-red-500/15", iconColor: "text-red-400", bar: "from-red-400 to-red-600" },
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
  const { iconBg, iconColor, bar } = TINT_CLASSES[tint];
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
          <div className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-xl", iconBg, iconColor)}>
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
