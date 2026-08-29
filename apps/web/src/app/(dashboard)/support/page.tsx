"use client";

import { useState } from "react";
import { LifeBuoy } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Badge,
  type BadgeVariant,
  Button,
  EmptyState,
  Input,
  Label,
  Select,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  Textarea,
  useToast,
} from "@/components/ui";
import { TicketDetailDialog } from "@/components/tickets/ticket-detail-dialog";
import {
  TICKET_CATEGORIES,
  type Ticket,
  type TicketCategory,
  type TicketStatus,
  useCreateTicket,
  useMyTickets,
} from "@/lib/hooks/useTickets";

const CATEGORY_LABEL: Record<TicketCategory, string> = {
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

const STATUS_BADGE: Record<TicketStatus, BadgeVariant> = {
  open: "live",
  in_progress: "neutral",
  resolved: "success",
  closed: "ended",
};

const EMPTY = { subject: "", description: "", category: "bug" as TicketCategory };

/**
 * Self-service "raise a ticket" page — any logged-in dashboard user (any
 * role) can report an error and it's routed straight to the Veerox platform
 * team (apps/api/routers/tickets.py's /admin/tickets queue), not the org's
 * own admin. `document.referrer` is sent as best-effort context (the page
 * the error happened on) since this form itself is a separate page.
 */
export default function SupportPage() {
  const [form, setForm] = useState(EMPTY);
  const createTicket = useCreateTicket();
  const { data, isLoading, isError, error, refetch } = useMyTickets();
  const tickets = data ?? [];
  const { toast } = useToast();
  const [selectedTicket, setSelectedTicket] = useState<Ticket | null>(null);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!form.subject.trim() || !form.description.trim()) return;

    createTicket.mutate(
      {
        subject: form.subject.trim(),
        description: form.description.trim(),
        category: form.category,
        page_url: typeof document !== "undefined" ? document.referrer || undefined : undefined,
      },
      {
        onSuccess: () => {
          toast({ title: "Ticket raised", description: "Our team has been notified.", variant: "success" });
          setForm(EMPTY);
        },
        onError: (err) =>
          toast({ title: "Could not raise ticket", description: err.message, variant: "error" }),
      }
    );
  }

  return (
    <div className="mx-auto max-w-4xl">
      <PageHeader
        title="Support"
        description="Hit an error or have an issue? Raise a ticket and our team will reach out directly."
      />

      <form
        onSubmit={handleSubmit}
        className="mb-8 flex flex-col gap-4 rounded-2xl border border-slate-200/80 bg-white p-5 shadow-card dark:border-slate-800 dark:bg-slate-900"
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <div className="sm:col-span-2">
            <Label htmlFor="ticket-subject">Subject *</Label>
            <Input
              id="ticket-subject"
              required
              value={form.subject}
              onChange={(e) => setForm((f) => ({ ...f, subject: e.target.value }))}
              placeholder="Short summary of the issue"
            />
          </div>
          <div>
            <Label htmlFor="ticket-category">Category</Label>
            <Select
              id="ticket-category"
              value={form.category}
              onChange={(value) => setForm((f) => ({ ...f, category: value as TicketCategory }))}
              className="w-full"
            >
              {TICKET_CATEGORIES.map((c) => (
                <option key={c} value={c}>
                  {CATEGORY_LABEL[c]}
                </option>
              ))}
            </Select>
          </div>
        </div>
        <div>
          <Label htmlFor="ticket-description">Description *</Label>
          <Textarea
            id="ticket-description"
            required
            rows={4}
            value={form.description}
            onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))}
            placeholder="What happened? Include any steps to reproduce."
          />
        </div>
        <div className="flex justify-end">
          <Button type="submit" variant="primary" loading={createTicket.isPending}>
            Raise ticket
          </Button>
        </div>
      </form>

      <h2 className="mb-3 text-sm font-semibold text-slate-700 dark:text-slate-200">Your tickets</h2>
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
                <SkeletonRows rows={3} cols={4} />
              </tbody>
            </Table>
          </div>
        }
        emptyFallback={
          <EmptyState icon={LifeBuoy} title="No tickets yet" description="Tickets you raise will show up here." />
        }
      >
        <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
          <Table>
            <thead>
              <TableRow isHeader>
                <TableHeader>Subject</TableHeader>
                <TableHeader>Category</TableHeader>
                <TableHeader>Status</TableHeader>
                <TableHeader>Raised</TableHeader>
              </TableRow>
            </thead>
            <tbody>
              {tickets.map((t) => (
                <TableRow key={t.id} onClick={() => setSelectedTicket(t)} className="cursor-pointer">
                  <TableCell className="font-medium text-slate-900 dark:text-slate-100">
                    {t.subject}
                  </TableCell>
                  <TableCell>{CATEGORY_LABEL[t.category]}</TableCell>
                  <TableCell>
                    <Badge variant={STATUS_BADGE[t.status]}>{STATUS_LABEL[t.status]}</Badge>
                  </TableCell>
                  <TableCell>{new Date(t.created_at).toLocaleDateString()}</TableCell>
                </TableRow>
              ))}
            </tbody>
          </Table>
        </div>
      </QueryBoundary>

      <TicketDetailDialog ticket={selectedTicket} onClose={() => setSelectedTicket(null)} />
    </div>
  );
}
