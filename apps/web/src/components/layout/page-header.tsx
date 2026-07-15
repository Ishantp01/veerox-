import type { ReactNode } from "react";

interface PageHeaderProps {
  title: string;
  description?: string;
  /** Optional right-aligned actions (buttons, etc.). */
  action?: ReactNode;
}

/**
 * Standard page title block. One `<h1>` per page (a11y landmark, UI plan §10).
 * Title left, optional action right.
 */
export function PageHeader({ title, description, action }: PageHeaderProps) {
  return (
    <div className="mb-8 flex items-start justify-between gap-4">
      <div>
        <h1 className="text-2xl font-extrabold tracking-tight text-slate-900 dark:text-slate-50">{title}</h1>
        {description && <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">{description}</p>}
      </div>
      {action && <div className="flex shrink-0 items-center gap-2">{action}</div>}
    </div>
  );
}
