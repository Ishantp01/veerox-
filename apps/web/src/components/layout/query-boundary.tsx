import type { ReactNode } from "react";
import { RefreshCw, WifiOff } from "lucide-react";
import { Button } from "@/components/ui/button";

interface QueryBoundaryProps {
  isLoading: boolean;
  isError: boolean;
  error?: Error | null;
  /** True when the request succeeded but returned no rows. */
  isEmpty?: boolean;
  /** Refetch callback wired to the error-state Retry button. */
  onRetry?: () => void;
  /** Shown while loading — pass a skeleton (e.g. <SkeletonRows/>). Defaults to a generic block. */
  loadingFallback?: ReactNode;
  /** Shown when isEmpty — pass an <EmptyState/>. */
  emptyFallback?: ReactNode;
  children: ReactNode;
}

/**
 * The standard load/empty/error/success wrapper every list + detail view uses
 * (UI plan §7.4). Keeps the four states visually consistent across pages
 * instead of each page hand-rolling them.
 */
export function QueryBoundary({
  isLoading,
  isError,
  error,
  isEmpty,
  onRetry,
  loadingFallback,
  emptyFallback,
  children,
}: QueryBoundaryProps) {
  if (isLoading) {
    return <>{loadingFallback ?? <div className="h-40 animate-pulse rounded-2xl bg-white" />}</>;
  }

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 px-6 py-16 text-center">
        <div className="rounded-2xl bg-gradient-to-b from-slate-100 to-slate-200 p-3.5 text-slate-400 ring-1 ring-slate-200 dark:from-slate-800 dark:to-slate-800/60 dark:text-slate-500 dark:ring-slate-700">
          <WifiOff size={22} aria-hidden />
        </div>
        <div className="space-y-1">
          <p className="text-sm font-semibold text-slate-700 dark:text-slate-200">Unable to load this data</p>
          <p className="mx-auto max-w-sm text-sm text-slate-500 dark:text-slate-400">
            Something went wrong on our end. Please try again in a moment.
          </p>
        </div>
        {onRetry && (
          <Button variant="outline" size="sm" onClick={onRetry} className="mt-1">
            <RefreshCw size={14} aria-hidden />
            Retry
          </Button>
        )}
      </div>
    );
  }

  if (isEmpty && emptyFallback) {
    return <>{emptyFallback}</>;
  }

  return <>{children}</>;
}
