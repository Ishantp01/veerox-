"use client";

import { ExternalLink } from "lucide-react";
import {
  Badge,
  type BadgeVariant,
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogTitle,
  Button,
  Select,
} from "@/components/ui";
import {
  TICKET_STATUSES,
  type AdminTicket,
  type Ticket,
  type TicketStatus,
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

const STATUS_BADGE: Record<TicketStatus, BadgeVariant> = {
  open: "live",
  in_progress: "neutral",
  resolved: "success",
  closed: "ended",
};

function isAdminTicket(ticket: Ticket | AdminTicket): ticket is AdminTicket {
  return "org_name" in ticket;
}

export interface TicketDetailDialogProps {
  /** The ticket to show, or null to keep the dialog closed. */
  ticket: (Ticket | AdminTicket) | null;
  onClose: () => void;
  /** When set, renders a status dropdown instead of a read-only badge —
   * pass this on the platform team's queue, omit it on the client-facing
   * self-service list where status is set by the team, not the org. */
  onStatusChange?: (status: TicketStatus) => void;
}

/** Full-detail view for a single ticket — the description and page context
 * a table row can't show without truncating. Shared by the client
 * self-service list (/support) and the platform team's queue
 * (/support-tickets); which fields render depends on which shape of ticket
 * is passed in and whether `onStatusChange` is supplied. */
export function TicketDetailDialog({ ticket, onClose, onStatusChange }: TicketDetailDialogProps) {
  const admin = ticket && isAdminTicket(ticket) ? ticket : null;

  return (
    <Dialog open={ticket !== null} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-xl">
        {ticket && (
          <>
            <DialogTitle>{ticket.subject}</DialogTitle>
            <DialogBody className="flex flex-col gap-4">
              <div className="flex flex-wrap items-center gap-2">
                <Badge variant="neutral" icon={null}>
                  {CATEGORY_LABEL[ticket.category] ?? ticket.category}
                </Badge>
                {onStatusChange ? (
                  <Select
                    value={ticket.status}
                    onChange={(v) => onStatusChange(v as TicketStatus)}
                    aria-label="Status"
                  >
                    {TICKET_STATUSES.map((s) => (
                      <option key={s} value={s}>
                        {STATUS_LABEL[s]}
                      </option>
                    ))}
                  </Select>
                ) : (
                  <Badge variant={STATUS_BADGE[ticket.status]}>{STATUS_LABEL[ticket.status]}</Badge>
                )}
              </div>

              {admin && (
                <div className="grid grid-cols-2 gap-x-4 gap-y-2 rounded-xl bg-slate-50 p-3 text-sm dark:bg-slate-800/50">
                  <div>
                    <dt className="text-xs text-slate-500 dark:text-slate-400">Organization</dt>
                    <dd className="font-medium text-slate-800 dark:text-slate-100">{admin.org_name}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-slate-500 dark:text-slate-400">Raised by</dt>
                    <dd className="font-medium text-slate-800 dark:text-slate-100">
                      {admin.account_user_name || admin.account_user_email}
                    </dd>
                  </div>
                </div>
              )}

              <div>
                <dt className="mb-1 text-xs text-slate-500 dark:text-slate-400">Description</dt>
                <dd className="whitespace-pre-wrap text-sm text-slate-700 dark:text-slate-200">
                  {ticket.description}
                </dd>
              </div>

              {ticket.page_url && (
                <div>
                  <dt className="mb-1 text-xs text-slate-500 dark:text-slate-400">Reported from</dt>
                  <dd>
                    <a
                      href={ticket.page_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 break-all text-sm text-primary-600 hover:underline dark:text-primary-400"
                    >
                      {ticket.page_url}
                      <ExternalLink size={12} aria-hidden className="shrink-0" />
                    </a>
                  </dd>
                </div>
              )}

              <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-xs text-slate-500 dark:text-slate-400">
                <div>Raised {new Date(ticket.created_at).toLocaleString()}</div>
                <div>Updated {new Date(ticket.updated_at).toLocaleString()}</div>
                {ticket.resolved_at && <div>Resolved {new Date(ticket.resolved_at).toLocaleString()}</div>}
              </div>
            </DialogBody>
            <DialogFooter>
              <Button variant="outline" onClick={onClose}>
                Close
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
