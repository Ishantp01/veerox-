import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { POLL, queryKeys } from "@/lib/query";
import type { HumanSupportResponse, Lead } from "@/lib/types";

export interface HumanSupportFilters {
  channel?: "voice" | "whatsapp";
}

function buildHumanSupportPath(filters?: HumanSupportFilters): string {
  if (filters?.channel) {
    return `/admin/human-support?channel=${encodeURIComponent(filters.channel)}`;
  }
  return "/admin/human-support";
}

/**
 * HumanSupport feed: persisted humanSupport leads + the live Redis handoff queue.
 * Polls every 3s (POLL.humanSupport) — this is time-sensitive operator work.
 * Optional `channel` filter is forwarded to the backend as ?channel=.
 *
 * GET /admin/human-support → { recent_leads, queue }
 */
export function useHumanSupport(filters?: HumanSupportFilters) {
  return useQuery<HumanSupportResponse>({
    queryKey: queryKeys.humanSupport(filters),
    queryFn: () => apiFetch<HumanSupportResponse>(buildHumanSupportPath(filters)),
    refetchInterval: POLL.humanSupport,
  });
}

/**
 * Claim a Human Support lead so the team knows who's handling it — first
 * claim wins, a 409 means someone else already grabbed it (surfaced via
 * `error.message`, see apiFetch's humanizeApiError).
 *
 * PATCH /admin/human-support/{leadId}/claim → Lead
 */
export function useClaimHumanSupport() {
  const queryClient = useQueryClient();
  return useMutation<Lead, Error, { leadId: string }>({
    mutationFn: ({ leadId }) =>
      apiFetch<Lead>(`/admin/human-support/${leadId}/claim`, { method: "PATCH" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["humanSupport"] });
    },
  });
}

/**
 * Connect any lead to a human from the Lead page — flags it as Human Support
 * and claims it for the caller (first claim wins; 409 if a teammate has it).
 *
 * POST /admin/leads/{leadId}/human-support → Lead
 */
export function useRequestHumanSupport() {
  const queryClient = useQueryClient();
  return useMutation<Lead, Error, { leadId: string }>({
    mutationFn: ({ leadId }) =>
      apiFetch<Lead>(`/admin/leads/${leadId}/human-support`, { method: "POST" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["humanSupport"] });
      queryClient.invalidateQueries({ queryKey: ["leads"] });
    },
  });
}
