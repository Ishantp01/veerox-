"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { BadgeCheck } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { ChannelBadge } from "@/components/conversations/channel-badge";
import {
  LEAD_QUALIFICATION_LABELS,
  LEAD_QUALIFICATION_OPTIONS,
} from "@/components/leads/qualification-badge";
import {
  EmptyState,
  Select,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
} from "@/components/ui";
import { useLeads, useUpdateLead } from "@/lib/hooks";
import { formatDateTime, formatPhone } from "@/lib/format";
import type { LeadQualificationStatus } from "@/lib/types";

const SELECT_CLS =
  "rounded-lg border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-semibold text-slate-700 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200";

/**
 * Cross-channel lead qualification pipeline — filter leads by qualification
 * stage and update it inline. Distinct from LeadsView's `status` (CRM stage),
 * which is a separate field on the same Lead row.
 */
export function QualificationView() {
  const router = useRouter();
  const [filter, setFilter] = useState<LeadQualificationStatus | "">("");
  const { data, isLoading, isError, error, refetch } = useLeads(
    filter ? { qualification_status: filter } : undefined,
  );
  const updateLead = useUpdateLead();
  const leads = data ?? [];

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Lead Qualification"
        description="Work leads through the qualification pipeline, independent of their CRM status."
        action={
          <Select
            value={filter}
            onChange={(v) => setFilter(v as LeadQualificationStatus | "")}
            aria-label="Filter by qualification stage"
          >
            <option value="">All stages</option>
            {LEAD_QUALIFICATION_OPTIONS.map((s) => (
              <option key={s} value={s}>
                {LEAD_QUALIFICATION_LABELS[s]}
              </option>
            ))}
          </Select>
        }
      />

      <QueryBoundary
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={leads.length === 0}
        onRetry={() => refetch()}
        loadingFallback={
          <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
            <Table>
              <tbody>
                <SkeletonRows rows={5} cols={5} />
              </tbody>
            </Table>
          </div>
        }
        emptyFallback={
          <EmptyState
            icon={BadgeCheck}
            title="No leads in this stage"
            description="Leads will appear here as they move through qualification."
          />
        }
      >
        <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
          <Table>
            <thead>
              <TableRow isHeader>
                <TableHeader>Name</TableHeader>
                <TableHeader>Phone</TableHeader>
                <TableHeader>Channel</TableHeader>
                <TableHeader>Stage</TableHeader>
                <TableHeader>Score</TableHeader>
                <TableHeader>Created</TableHeader>
              </TableRow>
            </thead>
            <tbody>
              {leads.map((lead) => (
                <TableRow key={lead.id}>
                  <TableCell>
                    <button
                      type="button"
                      onClick={() => router.push(`/crm/leads/${lead.id}`)}
                      className="font-semibold text-slate-800 hover:text-primary-600 dark:text-slate-100 dark:hover:text-primary-400"
                    >
                      {lead.name ?? "—"}
                    </button>
                  </TableCell>
                  <TableCell>
                    <span className="font-mono text-xs text-slate-600 dark:text-slate-400">
                      {formatPhone(lead.phone)}
                    </span>
                  </TableCell>
                  <TableCell>{lead.channel ? <ChannelBadge channel={lead.channel} /> : "—"}</TableCell>
                  <TableCell>
                    <Select
                      value={lead.qualification_status}
                      onChange={(v) =>
                        updateLead.mutate({
                          id: lead.id,
                          qualification_status: v as LeadQualificationStatus,
                        })
                      }
                      className={SELECT_CLS}
                      aria-label={`Qualification stage for ${lead.name ?? lead.phone ?? lead.id}`}
                    >
                      {LEAD_QUALIFICATION_OPTIONS.map((s) => (
                        <option key={s} value={s}>
                          {LEAD_QUALIFICATION_LABELS[s]}
                        </option>
                      ))}
                    </Select>
                  </TableCell>
                  <TableCell className="text-xs text-slate-500">
                    {lead.qualification_score ?? "—"}
                  </TableCell>
                  <TableCell className="text-xs text-slate-500">
                    {formatDateTime(lead.created_at)}
                  </TableCell>
                </TableRow>
              ))}
            </tbody>
          </Table>
        </div>
      </QueryBoundary>
    </div>
  );
}
