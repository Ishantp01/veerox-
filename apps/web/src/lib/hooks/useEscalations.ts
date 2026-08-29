import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { POLL, queryKeys } from "@/lib/query";
import type { EscalationsResponse, Lead } from "@/lib/types";

export interface EscalationFilters {
  channel?: "voice" | "whatsapp";
}

function buildEscalationsPath(filters?: EscalationFilters): string {
  if (filters?.channel) {
    return `/admin/escalations?channel=${encodeURIComponent(filters.channel)}`;
  }
  return "/admin/escalations";
}

/**
 * Escalations feed: persisted escalation leads + the live Redis handoff queue.
 * Polls every 3s (POLL.escalations) — this is time-sensitive operator work.
 * Optional `channel` filter is forwarded to the backend as ?channel=.
 *
 * GET /admin/escalations → { recent_leads, queue }
 */
export function useEscalations(filters?: EscalationFilters) {
  return useQuery<EscalationsResponse>({
    queryKey: queryKeys.escalations(filters),
    queryFn: () => apiFetch<EscalationsResponse>(buildEscalationsPath(filters)),
    refetchInterval: POLL.escalations,
  });
}

/**
 * Claim an escalation lead so the team knows who's handling it — first
 * claim wins, a 409 means someone else already grabbed it (surfaced via
 * `error.message`, see apiFetch's humanizeApiError).
 *
 * PATCH /admin/escalations/{leadId}/claim → Lead
 */
export function useClaimEscalation() {
  const queryClient = useQueryClient();
  return useMutation<Lead, Error, { leadId: string }>({
    mutationFn: ({ leadId }) =>
      apiFetch<Lead>(`/admin/escalations/${leadId}/claim`, { method: "PATCH" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["escalations"] });
    },
  });
}
