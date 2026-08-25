"use client";

import { useRouter } from "next/navigation";
import { Badge, Select, Table, TableHeader, TableRow, TableCell } from "@/components/ui";
import { useUpdateLead } from "@/lib/hooks";
import { formatDateTime, formatPhone } from "@/lib/format";
import type { Lead, LeadQualificationStatus } from "@/lib/types";
import { IntentBadge } from "./intent-badge";
import { StatusBadge } from "./status-badge";
import { LEAD_QUALIFICATION_LABELS, LEAD_QUALIFICATION_OPTIONS } from "./qualification-badge";

const QUALIFICATION_SELECT_CLS =
  "rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

export interface LeadTableProps {
  leads: Lead[];
  /** Base path for row navigation, e.g. "/whatsapp/leads". Rows are inert without it. */
  detailBasePath?: string;
}

/**
 * Presentational lead table (UI plan §7.2) — aware of the Lead type but not of
 * fetching. Columns: Name, Phone, Intent, Status, Review Stage, Created. Rows
 * navigate to `${detailBasePath}/${lead.id}` when provided (dashboard/CRM
 * detail view). "Review Stage" (Lead.qualification_status) is a second,
 * independent field from "Status" (Lead.status) — a lead can be "Contacted"
 * in the pipeline while already "Qualified" in review, or vice versa. Named
 * differently from "Status" here (not just "Qualification") specifically so
 * the two columns don't read as the same concept — see each header's
 * `title` tooltip for the exact distinction. Edited inline here rather than
 * on a separate page since it's the same underlying Lead row.
 */
export function LeadTable({ leads, detailBasePath }: LeadTableProps) {
  const router = useRouter();
  const updateLead = useUpdateLead();

  return (
    <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
      <Table>
        <thead>
          <TableRow isHeader>
            <TableHeader>Name</TableHeader>
            <TableHeader>Phone</TableHeader>
            <TableHeader>Intent</TableHeader>
            <TableHeader>Tags</TableHeader>
            <TableHeader title="Where this lead sits in your sales pipeline: New → Contacted → Qualified → Converted/Lost.">
              Status
            </TableHeader>
            <TableHeader title="A separate, rep-driven review of whether this lead is worth pursuing — independent of the pipeline Status.">
              Review Stage
            </TableHeader>
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
                  <span className="font-semibold text-slate-800 dark:text-slate-100">{lead.name ?? "—"}</span>
                </TableCell>
                <TableCell>
                  <span className="font-mono text-xs text-slate-600 dark:text-slate-400">
                    {formatPhone(lead.phone)}
                  </span>
                </TableCell>
                <TableCell>
                  <IntentBadge intent={lead.intent} />
                </TableCell>
                <TableCell>
                  {lead.tags && lead.tags.length > 0 ? (
                    <div className="flex flex-wrap gap-1">
                      {lead.tags.map((tag) => (
                        <Badge key={tag} variant="neutral" icon={null}>
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    <span className="text-slate-400 dark:text-slate-600">—</span>
                  )}
                </TableCell>
                <TableCell>
                  <StatusBadge status={lead.status} />
                </TableCell>
                <TableCell onClick={(e) => e.stopPropagation()}>
                  <Select
                    value={lead.qualification_status}
                    onChange={(v) =>
                      updateLead.mutate({
                        id: lead.id,
                        qualification_status: v as LeadQualificationStatus,
                      })
                    }
                    className={QUALIFICATION_SELECT_CLS}
                    aria-label={`Review stage for ${lead.name ?? lead.phone ?? lead.id}`}
                  >
                    {LEAD_QUALIFICATION_OPTIONS.map((s) => (
                      <option key={s} value={s}>
                        {LEAD_QUALIFICATION_LABELS[s]}
                      </option>
                    ))}
                  </Select>
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
