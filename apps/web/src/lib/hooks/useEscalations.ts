import { useQuery } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { POLL, queryKeys } from "@/lib/query";
import type { EscalationsResponse } from "@/lib/types";

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
 * Polls every 5s (POLL.escalations) — this is time-sensitive operator work.
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
