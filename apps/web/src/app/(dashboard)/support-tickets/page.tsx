"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { LifeBuoy } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  EmptyState,
  Select,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
} from "@/components/ui";
import { TicketDetailDialog } from "@/components/tickets/ticket-detail-dialog";
import { useAuth } from "@/lib/auth-context";
import {
  TICKET_STATUSES,
  type AdminTicket,
  type TicketStatus,
  useAdminTickets,
  useUpdateTicketStatus,
} from "@/lib/hooks/useTickets";

const CATEGORY_LABEL: Record<string, string> = {
  bug: "Bug",
  billing: "Billing",
  feature_request: "Feature request",
  urgent: "Urgent",
  other: "Other",
};

const STATUS_LABEL: Record<TicketStatus, string> = {
  open: "Open",
  in_progress: "In progress",
  resolved: "Resolved",
  closed: "Closed",
};

/**
 * Platform-wide support ticket queue — every org's tickets in one place.
 * Visible to any Veerox staff account: the platform admin (superuser) and
 * any teammate they've invited onto the platform's own org (`is_platform_org`
 * — see apps/api/routers/tickets.py's GET /admin/tickets, guarded by
 * `verify_platform_team_member`), not just the superuser flag alone. Wider
 * than the Organizations page's superuser-only gate, since triaging tickets
 * doesn't need that page's billing/token-regeneration powers.
 */
export default function SupportTicketsPage() {
  const { user, status: authStatus } = useAuth();
  const router = useRouter();
  const [statusFilter, setStatusFilter] = useState<TicketStatus | "all">("all");
  const { data, isLoading, isError, error, refetch } = useAdminTickets(statusFilter);
  const tickets = data ?? [];
  const updateStatus = useUpdateTicketStatus();
  const canView = user?.is_superuser || user?.is_platform_org;
  const [selectedTicket, setSelectedTicket] = useState<AdminTicket | null>(null);

  useEffect(() => {
    if (authStatus === "authenticated" && !canView) {
      router.replace("/");
    }
  }, [authStatus, canView, router]);

  if (!canView) return null;

  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader
        title="Support Tickets"
        description="Every ticket raised across the platform — visible to the Veerox team."
        action={
          <Select value={statusFilter} onChange={(v) => setStatusFilter(v as TicketStatus | "all")}>
            <option value="all">All statuses</option>
            {TICKET_STATUSES.map((s) => (
              <option key={s} value={s}>
                {STATUS_LABEL[s]}
              </option>
            ))}
          </Select>
        }
      />

      <QueryBoundary
        isLoading={isLoading}
        isError={isError}
        error={error}
        isEmpty={tickets.length === 0}
        onRetry={() => refetch()}
        loadingFallback={
          <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
            <Table>
              <tbody>
                <SkeletonRows rows={5} cols={6} />
              </tbody>
            </Table>
          </div>
        }
        emptyFallback={
          <EmptyState icon={LifeBuoy} title="No tickets" description="Tickets raised by any org will show up here." />
        }
      >
        <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
          <Table>
            <thead>
              <TableRow isHeader>
                <TableHeader>Organization</TableHeader>
                <TableHeader>Raised by</TableHeader>
                <TableHeader>Subject</TableHeader>
                <TableHeader>Category</TableHeader>
                <TableHeader>Raised</TableHeader>
                <TableHeader>Status</TableHeader>
              </TableRow>
            </thead>
            <tbody>
              {tickets.map((t) => (
                <TableRow key={t.id} onClick={() => setSelectedTicket(t)} className="cursor-pointer">
                  <TableCell className="font-medium text-slate-900 dark:text-slate-100">
                    {t.org_name}
                  </TableCell>
                  <TableCell>{t.account_user_name || t.account_user_email}</TableCell>
                  <TableCell>
                    <div>{t.subject}</div>
                    <div className="mt-0.5 max-w-xs truncate text-xs text-slate-500 dark:text-slate-400">
                      {t.description}
                    </div>
                  </TableCell>
                  <TableCell>{CATEGORY_LABEL[t.category] ?? t.category}</TableCell>
                  <TableCell>{new Date(t.created_at).toLocaleString()}</TableCell>
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <Select
                      value={t.status}
                      onChange={(v) =>
                        updateStatus.mutate({ ticketId: t.id, status: v as TicketStatus })
                      }
                    >
                      {TICKET_STATUSES.map((s) => (
                        <option key={s} value={s}>
                          {STATUS_LABEL[s]}
                        </option>
                      ))}
                    </Select>
                  </TableCell>
                </TableRow>
              ))}
            </tbody>
          </Table>
        </div>
      </QueryBoundary>

      <TicketDetailDialog
        ticket={selectedTicket}
        onClose={() => setSelectedTicket(null)}
        onStatusChange={(status) => {
          if (!selectedTicket) return;
          updateStatus.mutate({ ticketId: selectedTicket.id, status });
          setSelectedTicket((t) => (t ? { ...t, status } : t));
        }}
      />
    </div>
  );
}
