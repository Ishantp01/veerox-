import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "./button";

export interface PaginationProps {
  /** 0-indexed current page. */
  page: number;
  pageSize: number;
  /** Number of rows returned for the current page. */
  rowCount: number;
  /** True when the current page came back full — there may be more rows past it. */
  hasNextPage: boolean;
  onPrev: () => void;
  onNext: () => void;
  className?: string;
}

/**
 * Prev/Next pager for admin list tables (leads, conversations, ...) whose
 * `GET` endpoints take `limit`/`offset` but never return a total count. With
 * no total, "has a next page" is inferred from the page coming back full
 * (`rowCount === pageSize`) — the standard heuristic when the backend won't
 * do a separate COUNT query per page.
 */
export function Pagination({ page, pageSize, rowCount, hasNextPage, onPrev, onNext, className }: PaginationProps) {
  const from = rowCount === 0 ? 0 : page * pageSize + 1;
  const to = page * pageSize + rowCount;

  return (
    <div className={`flex items-center justify-between gap-4 px-1 py-3 text-xs text-slate-500 dark:text-slate-400 ${className ?? ""}`}>
      <span>
        {rowCount === 0 ? "No rows" : `Showing ${from}–${to}`}
      </span>
      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" onClick={onPrev} disabled={page === 0}>
          <ChevronLeft size={14} aria-hidden />
          Prev
        </Button>
        <Button variant="outline" size="sm" onClick={onNext} disabled={!hasNextPage}>
          Next
          <ChevronRight size={14} aria-hidden />
        </Button>
      </div>
    </div>
  );
}
