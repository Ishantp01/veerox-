import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type {
  ConversationEngineStatus,
  ConversationTurn,
  KnowledgeItem,
  KnowledgeKind,
  KnowledgeLanguage,
} from "@/lib/types";

/**
 * Conversation Intelligence Engine (apps/api/routers/conversation_engine.py).
 * Org admins only — a plain member's session gets 403.
 */

/** GET /conversation-engine/status → this org's behaviour mode. */
export function useConversationEngineStatus() {
  return useQuery<ConversationEngineStatus>({
    queryKey: queryKeys.conversationEngineStatus(),
    queryFn: () => apiFetch<ConversationEngineStatus>("/conversation-engine/status"),
  });
}

export interface ConversationTurnFilters {
  mode?: "live" | "shadow";
  fallbacksOnly?: boolean;
  limit?: number;
  offset?: number;
}

/** GET /conversation-engine/turns — the per-turn log, newest first. */
export function useConversationTurns(filters: ConversationTurnFilters = {}) {
  const params = new URLSearchParams();
  if (filters.mode) params.set("mode", filters.mode);
  if (filters.fallbacksOnly) params.set("fallbacks_only", "true");
  params.set("limit", String(filters.limit ?? 50));
  if (filters.offset) params.set("offset", String(filters.offset));

  return useQuery<ConversationTurn[]>({
    queryKey: queryKeys.conversationEngineTurns(filters),
    queryFn: () => apiFetch<ConversationTurn[]>(`/conversation-engine/turns?${params.toString()}`),
    refetchInterval: 15_000,
  });
}

/** GET /conversation-engine/knowledge → approved knowledge items. */
export function useKnowledgeItems() {
  return useQuery<KnowledgeItem[]>({
    queryKey: queryKeys.conversationEngineKnowledge(),
    queryFn: () => apiFetch<KnowledgeItem[]>("/conversation-engine/knowledge"),
  });
}

export interface KnowledgeInput {
  kind: KnowledgeKind;
  title: string;
  body: string;
  keywords?: string | null;
  language?: KnowledgeLanguage | null;
  active?: boolean;
}

/** POST /conversation-engine/knowledge */
export function useCreateKnowledgeItem() {
  const queryClient = useQueryClient();
  return useMutation<KnowledgeItem, Error, KnowledgeInput>({
    mutationFn: (body) =>
      apiFetch<KnowledgeItem>("/conversation-engine/knowledge", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.conversationEngineKnowledge() });
    },
  });
}

/** PATCH /conversation-engine/knowledge/{id} */
export function useUpdateKnowledgeItem() {
  const queryClient = useQueryClient();
  return useMutation<KnowledgeItem, Error, { id: string } & Partial<KnowledgeInput>>({
    mutationFn: ({ id, ...body }) =>
      apiFetch<KnowledgeItem>(`/conversation-engine/knowledge/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.conversationEngineKnowledge() });
    },
  });
}

/** DELETE /conversation-engine/knowledge/{id} */
export function useDeleteKnowledgeItem() {
  const queryClient = useQueryClient();
  return useMutation<unknown, Error, string>({
    mutationFn: (id) => apiFetch(`/conversation-engine/knowledge/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.conversationEngineKnowledge() });
    },
  });
}
