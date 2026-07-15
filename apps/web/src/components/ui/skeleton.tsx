import { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/** A pulsing placeholder block used while content loads. */
export function Skeleton({
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden
      className={cn("animate-pulse rounded-md bg-slate-200 dark:bg-slate-800", className)}
      {...props}
    />
  );
}

export interface SkeletonRowsProps {
  /** Number of placeholder rows to render. */
  rows?: number;
  /** Number of cells per row. */
  cols?: number;
  className?: string;
}

/**
 * Skeleton loading state for tables (plan §7.4 — never a bare spinner for
 * tables). Renders `<tr>`/`<td>` so it can drop into a `<tbody>`.
 */
export function SkeletonRows({ rows = 5, cols = 4, className }: SkeletonRowsProps) {
  return (
    <>
      {Array.from({ length: rows }).map((_, r) => (
        <tr key={r} className={cn("border-t border-slate-100 dark:border-slate-800", className)}>
          {Array.from({ length: cols }).map((__, c) => (
            <td key={c} className="px-5 py-3.5">
              <Skeleton className="h-4 w-full max-w-[160px]" />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}
