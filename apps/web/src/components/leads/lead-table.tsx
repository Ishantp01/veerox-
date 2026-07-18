"use client";

import { useRouter } from "next/navigation";
import { Table, TableHeader, TableRow, TableCell } from "@/components/ui";
import { formatDateTime, formatPhone } from "@/lib/format";
import type { Lead } from "@/lib/types";
import { IntentBadge } from "./intent-badge";
import { StatusBadge } from "./status-badge";

export interface LeadTableProps {
  leads: Lead[];
  /** Base path for row navigation, e.g. "/whatsapp/leads". Rows are inert without it. */
  detailBasePath?: string;
}

/**
 * Presentational lead table (UI plan §7.2) — aware of the Lead type but not of
 * fetching. Columns: Name, Phone, Intent, Status, Created. Rows navigate to
 * `${detailBasePath}/${lead.id}` when provided (dashboard/CRM detail view).
 */
export function LeadTable({ leads, detailBasePath }: LeadTableProps) {
  const router = useRouter();

  return (
    <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
      <Table>
        <thead>
          <TableRow isHeader>
            <TableHeader>Name</TableHeader>
            <TableHeader>Phone</TableHeader>
            <TableHeader>Intent</TableHeader>
            <TableHeader>Status</TableHeader>
            <TableHeader>Created</TableHeader>
          </TableRow>
        </thead>
        <tbody>
          {leads.map((lead) => {
            const href = detailBasePath ? `${detailBasePath}/${lead.id}` : undefined;
            return (
              <TableRow
                key={lead.id}
                role={href ? "link" : undefined}
                tabIndex={href ? 0 : undefined}
                aria-label={href ? `Open lead ${lead.name ?? lead.phone ?? lead.id}` : undefined}
                onClick={href ? () => router.push(href) : undefined}
                onKeyDown={
                  href
                    ? (e) => {
                        if (e.key === "Enter" || e.key === " ") {
                          e.preventDefault();
                          router.push(href);
                        }
                      }
                    : undefined
                }
                className={
                  href
                    ? "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary-500"
                    : undefined
                }
              >
                <TableCell>
                  <span className="font-semibold text-slate-800">{lead.name ?? "—"}</span>
                </TableCell>
                <TableCell>
                  <span className="font-mono text-xs text-slate-600">
                    {formatPhone(lead.phone)}
                  </span>
                </TableCell>
                <TableCell>
                  <IntentBadge intent={lead.intent} />
                </TableCell>
                <TableCell>
                  <StatusBadge status={lead.status} />
                </TableCell>
                <TableCell className="text-xs text-slate-500">
                  {formatDateTime(lead.created_at)}
                </TableCell>
              </TableRow>
            );
          })}
        </tbody>
      </Table>
    </div>
  );
}
