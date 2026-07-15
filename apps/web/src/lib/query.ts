import { QueryClient } from "@tanstack/react-query";

/**
 * Single QueryClient factory. Defaults tuned for an operator dashboard:
 * - retry transient failures 3x with backoff, but never retry an auth
 *   failure (401/403) — a bad/missing admin token will never succeed on
 *   retry, so retrying just delays the error state the user needs to see
 * - don't refetch on window focus (operators keep the tab open; polling covers freshness)
 * - 30s staleTime baseline; individual queries override refetchInterval for live data
 */
export function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (failureCount, error) => {
          const status = (error as { status?: number } | undefined)?.status;
          if (status === 401 || status === 403) return false;
          return failureCount < 3;
        },
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
        refetchOnWindowFocus: false,
        staleTime: 30_000,
      },
    },
  });
}

/**
 * Centralized query keys — the single source of truth for cache identity.
 * Mutations invalidate by these keys so the UI reconciles automatically.
 *
 * Convention mirrors the UI plan §6.2.
 */
export const queryKeys = {
  stats: () => ["stats"] as const,
  conversations: (filters?: { channel?: string; status?: string }) =>
    ["conversations", filters ?? {}] as const,
  conversationMessages: (id: string) => ["conversation", id, "messages"] as const,
  leads: (filters?: { intent?: string; channel?: string; status?: string }) =>
    ["leads", filters ?? {}] as const,
  lead: (id: string) => ["leads", "detail", id] as const,
  escalations: (filters?: { channel?: string }) => ["escalations", filters ?? {}] as const,
  killSwitch: () => ["kill-switch"] as const,
  prompts: () => ["prompts"] as const,
  tools: () => ["tools"] as const,
  settings: () => ["settings"] as const,
  whatsappSettings: () => ["settings", "whatsapp"] as const,
  callingSettings: () => ["settings", "calling"] as const,
  campaigns: () => ["campaigns"] as const,
  campaign: (id: string) => ["campaigns", "detail", id] as const,
  reportsTimeseries: (days: number) => ["reports", "timeseries", days] as const,
  reportsCampaigns: () => ["reports", "campaigns"] as const,
};

/**
 * Polling intervals (ms) per the UI plan §6.3 live-data strategy.
 * Centralized so the cadence is consistent and tunable in one place.
 */
export const POLL = {
  liveConversation: 5_000,
  escalations: 5_000,
  dashboard: 10_000,
  conversationList: 10_000,
  leads: 30_000,
  campaigns: 5_000,
} as const;
