import { type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export type StatTint = "primary" | "sky" | "emerald" | "rose" | "amber";

const TINT_CLASSES: Record<StatTint, { chip: string; accent: string }> = {
  primary: { chip: "bg-primary-50 text-primary-600", accent: "from-primary-500 to-violet-500" },
  sky: { chip: "bg-sky-50 text-sky-600", accent: "from-sky-500 to-cyan-500" },
  emerald: { chip: "bg-emerald-50 text-emerald-600", accent: "from-emerald-500 to-teal-500" },
  rose: { chip: "bg-rose-50 text-rose-600", accent: "from-rose-500 to-pink-500" },
  amber: { chip: "bg-amber-50 text-amber-600", accent: "from-amber-500 to-orange-500" },
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
  const { chip, accent } = TINT_CLASSES[tint];
  return (
    <div
      className={cn(
        "group relative overflow-hidden rounded-2xl border border-slate-200 bg-white p-6 shadow-elevated transition-all duration-200 hover:-translate-y-0.5 hover:shadow-elevated-lg",
        className,
      )}
    >
      <div
        aria-hidden
        className={cn(
          "absolute inset-x-0 top-0 h-1 bg-gradient-to-r opacity-90 transition-opacity group-hover:opacity-100",
          accent,
        )}
      />
      <div className="flex items-start justify-between gap-3">
        <p className="text-[11px] font-bold uppercase tracking-widest text-slate-400">
          {label}
        </p>
        {Icon && (
          <div className={cn("flex h-9 w-9 shrink-0 items-center justify-center rounded-xl", chip)}>
            <Icon size={16} aria-hidden />
          </div>
        )}
      </div>
      <p className="mt-4 text-3xl font-extrabold tracking-tight text-slate-900">
        {value}
      </p>
      {sublabel && <p className="mt-2 text-xs text-slate-400">{sublabel}</p>}
    </div>
  );
}

export default StatCard;
