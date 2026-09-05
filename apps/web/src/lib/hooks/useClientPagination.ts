import { useEffect, useMemo, useState } from "react";

export interface ClientPagination<T> {
  /** Current 0-indexed page, clamped to the available range. */
  page: number;
  /** The rows to render for the current page. */
  pageRows: T[];
  pageSize: number;
  /** Rows on the current page (for the "Showing X–Y" label). */
  rowCount: number;
  hasNextPage: boolean;
  onPrev: () => void;
  onNext: () => void;
}

/**
 * Client-side pager for admin list views whose `GET` endpoints already return
 * the full (org-scoped) result set. Slices the array and hands back props that
 * drop straight into `<Pagination {...} />`.
 *
 * `resetKey` should be whatever the caller filters/searches by — changing it
 * snaps back to page 1 so you're never left on a page that no longer exists.
 */
export function useClientPagination<T>(
  rows: T[],
  pageSize = 20,
  resetKey?: unknown,
): ClientPagination<T> {
  const [page, setPage] = useState(0);

  useEffect(() => {
    setPage(0);
  }, [resetKey]);

  const total = rows.length;
  const lastPage = Math.max(0, Math.ceil(total / pageSize) - 1);
  const safePage = Math.min(page, lastPage);

  const pageRows = useMemo(
    () => rows.slice(safePage * pageSize, safePage * pageSize + pageSize),
    [rows, safePage, pageSize],
  );

  return {
    page: safePage,
    pageRows,
    pageSize,
    rowCount: pageRows.length,
    hasNextPage: (safePage + 1) * pageSize < total,
    onPrev: () => setPage((p) => Math.max(0, p - 1)),
    onNext: () => setPage((p) => p + 1),
  };
}
