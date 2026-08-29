import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export const TICKET_CATEGORIES = ["bug", "billing", "feature_request", "urgent", "other"] as const;
export type TicketCategory = (typeof TICKET_CATEGORIES)[number];

export const TICKET_STATUSES = ["open", "in_progress", "resolved", "closed"] as const;
export type TicketStatus = (typeof TICKET_STATUSES)[number];

export interface Ticket {
  id: string;
  org_id: string;
  account_user_id: string;
  subject: string;
  description: string;
  category: TicketCategory;
  status: TicketStatus;
  page_url: string | null;
  created_at: string;
  updated_at: string;
  resolved_at: string | null;
}

export interface AdminTicket extends Ticket {
  org_name: string;
  account_user_email: string;
  account_user_name: string | null;
}

/** GET /tickets → Ticket[], scoped to the caller's own org. */
export function useMyTickets() {
  return useQuery<Ticket[]>({
    queryKey: ["tickets", "mine"],
    queryFn: () => apiFetch<Ticket[]>("/tickets"),
  });
}

export interface CreateTicketInput {
  subject: string;
  description: string;
  category: TicketCategory;
  page_url?: string;
}

/** POST /tickets → Ticket. Any logged-in dashboard user can raise a ticket. */
export function useCreateTicket() {
  const queryClient = useQueryClient();
  return useMutation<Ticket, Error, CreateTicketInput>({
    mutationFn: (body) =>
      apiFetch<Ticket>("/tickets", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["tickets", "mine"] });
    },
  });
}

/** GET /admin/tickets → AdminTicket[] (platform-admin only) — every org's
 * tickets, optionally filtered by status. */
export function useAdminTickets(status?: TicketStatus | "all") {
  const query = status && status !== "all" ? `?status=${status}` : "";
  return useQuery<AdminTicket[]>({
    queryKey: ["admin", "tickets", status ?? "all"],
    queryFn: () => apiFetch<AdminTicket[]>(`/admin/tickets${query}`),
  });
}

/** PATCH /admin/tickets/{id} → AdminTicket (platform-admin only). */
export function useUpdateTicketStatus() {
  const queryClient = useQueryClient();
  return useMutation<AdminTicket, Error, { ticketId: string; status: TicketStatus }>({
    mutationFn: ({ ticketId, status }) =>
      apiFetch<AdminTicket>(`/admin/tickets/${ticketId}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "tickets"] });
    },
  });
}
