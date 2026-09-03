import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { POLL, queryKeys } from "@/lib/query";
import type { Lead, LeadDetail, LeadQualificationStatus, LeadStatus } from "@/lib/types";

export interface LeadFilters {
  intent?: string;
  channel?: "voice" | "whatsapp";
  status?: LeadStatus;
  qualification_status?: LeadQualificationStatus;
  tag?: string;
  /** Unified search box — matches against intent OR tags. */
  search?: string;
  /** Page size — the backend defaults to 50 and caps at 200. */
  limit?: number;
  /** Row offset for pagination — 0 is the first page. */
  offset?: number;
}

function buildLeadsPath(filters?: LeadFilters): string {
  const params = new URLSearchParams();
  if (filters?.intent) params.set("intent", filters.intent);
  if (filters?.channel) params.set("channel", filters.channel);
  if (filters?.status) params.set("status", filters.status);
  if (filters?.qualification_status) params.set("qualification_status", filters.qualification_status);
  if (filters?.tag) params.set("tag", filters.tag);
  if (filters?.search) params.set("search", filters.search);
  if (filters?.limit !== undefined) params.set("limit", String(filters.limit));
  if (filters?.offset !== undefined) params.set("offset", String(filters.offset));
  const qs = params.toString();
  return qs ? `/admin/leads?${qs}` : "/admin/leads";
}

/**
 * Captured leads, newest first. Polls every 30s (POLL.leads). Optional
 * `intent`/`channel`/`status` filters are forwarded to the backend as query
 * params. Pass `limit`/`offset` to page through orgs with more leads than
 * one page — omitting them falls back to the backend's default first page
 * (50 rows), so existing callers keep working unchanged.
 *
 * GET /admin/leads → Lead[]
 */
export function useLeads(filters?: LeadFilters) {
  return useQuery<Lead[]>({
    queryKey: queryKeys.leads(filters),
    queryFn: () => apiFetch<Lead[]>(buildLeadsPath(filters)),
    refetchInterval: POLL.leads,
    // Keep the previous page's rows on screen while the next page loads —
    // otherwise every Prev/Next click flashes the loading skeleton because
    // a new offset means a fresh (empty) cache entry.
    placeholderData: keepPreviousData,
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
  qualification_status?: LeadQualificationStatus;
  qualification_score?: number | null;
  qualification_notes?: string | null;
  tags?: string[] | null;
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
