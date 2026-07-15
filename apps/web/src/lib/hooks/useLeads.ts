import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { POLL, queryKeys } from "@/lib/query";
import type { Lead, LeadDetail, LeadStatus } from "@/lib/types";

export interface LeadFilters {
  intent?: string;
  channel?: "voice" | "whatsapp";
  status?: LeadStatus;
}

function buildLeadsPath(filters?: LeadFilters): string {
  const params = new URLSearchParams();
  if (filters?.intent) params.set("intent", filters.intent);
  if (filters?.channel) params.set("channel", filters.channel);
  if (filters?.status) params.set("status", filters.status);
  const qs = params.toString();
  return qs ? `/admin/leads?${qs}` : "/admin/leads";
}

/**
 * Captured leads, newest first. Polls every 30s (POLL.leads). Optional
 * `intent`/`channel`/`status` filters are forwarded to the backend as query
 * params.
 *
 * GET /admin/leads → Lead[]
 */
export function useLeads(filters?: LeadFilters) {
  return useQuery<Lead[]>({
    queryKey: queryKeys.leads(filters),
    queryFn: () => apiFetch<Lead[]>(buildLeadsPath(filters)),
    refetchInterval: POLL.leads,
  });
}

/**
 * Single lead plus its conversation history (dashboard/CRM detail view).
 * Disabled until `id` is provided.
 *
 * GET /admin/leads/{id} → LeadDetail
 */
export function useLead(id: string | undefined | null) {
  return useQuery<LeadDetail>({
    queryKey: queryKeys.lead(id ?? ""),
    queryFn: () => apiFetch<LeadDetail>(`/admin/leads/${id}`),
    enabled: Boolean(id),
  });
}

export interface LeadUpdateInput {
  id: string;
  status?: LeadStatus;
  follow_up_at?: string | null;
  follow_up_note?: string | null;
}

/**
 * Update a lead's status and/or follow-up. Only the fields present on the
 * input object are sent, so passing just `{ id, status }` leaves follow-up
 * untouched — pass `follow_up_at: null` / `follow_up_note: null` to clear
 * them.
 *
 * PATCH /admin/leads/{id} → Lead
 */
export function useUpdateLead() {
  const queryClient = useQueryClient();

  return useMutation<Lead, Error, LeadUpdateInput>({
    mutationFn: ({ id, ...body }) =>
      apiFetch<Lead>(`/admin/leads/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      // Single prefix invalidates every leads list variant plus all lead
      // detail queries (queryKeys.lead(id) shares the "leads" root key).
      queryClient.invalidateQueries({ queryKey: ["leads"] });
    },
  });
}
