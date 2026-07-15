import { ReactNode } from "react";
import { type LucideIcon } from "lucide-react";
import { cn } from "@/lib/utils";

export interface EmptyStateProps {
  /** Lucide icon component shown above the title. */
  icon?: LucideIcon;
  title: string;
  description?: ReactNode;
  /** Optional call-to-action (e.g. a `<Button>`). */
  action?: ReactNode;
  className?: string;
}

/**
 * Centered empty state with a dashed border and muted text (plan §7.4).
 * Used as the "no data" state for lists.
 */
export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-slate-300 bg-slate-50/50 px-6 py-12 text-center dark:border-slate-700 dark:bg-slate-900/50",
        className,
      )}
    >
      {Icon && (
        <div className="rounded-full bg-slate-100 p-3 text-slate-400 dark:bg-slate-800 dark:text-slate-500">
          <Icon size={24} aria-hidden />
        </div>
      )}
      <div className="space-y-1">
        <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">{title}</p>
        {description && (
          <p className="mx-auto max-w-sm text-sm text-slate-500 dark:text-slate-400">{description}</p>
        )}
      </div>
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}
