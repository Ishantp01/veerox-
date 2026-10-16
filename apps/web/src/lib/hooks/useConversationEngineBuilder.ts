import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type {
  ConfigCheck,
  ConfigOverview,
  ConfigVersion,
  ConversationEngineStatus,
  ConversationMode,
  ConversationTurn,
  EngineAuditEvent,
  EngineMetrics,
  ScenarioConfig,
  SimulateInput,
  SimulateResult,
} from "@/lib/types";

/**
 * Scenario Builder, simulator, audit log and metrics for the Conversation
 * Engine (apps/api/routers/conversation_engine_config.py). Org admins only.
 */

function invalidateConfig(queryClient: ReturnType<typeof useQueryClient>) {
  queryClient.invalidateQueries({ queryKey: queryKeys.conversationEngineConfig() });
  queryClient.invalidateQueries({ queryKey: queryKeys.conversationEngineVersions() });
  queryClient.invalidateQueries({ queryKey: queryKeys.conversationEngineStatus() });
  queryClient.invalidateQueries({ queryKey: queryKeys.conversationEngineAudit() });
}

/** GET /conversation-engine/config → active version, draft and the list a new draft starts from. */
export function useEngineConfig() {
  return useQuery<ConfigOverview>({
    queryKey: queryKeys.conversationEngineConfig(),
    queryFn: () => apiFetch<ConfigOverview>("/conversation-engine/config"),
  });
}

/** GET /conversation-engine/config/versions → published versions, newest first. */
export function useConfigVersions() {
  return useQuery<ConfigVersion[]>({
    queryKey: queryKeys.conversationEngineVersions(),
    queryFn: () => apiFetch<ConfigVersion[]>("/conversation-engine/config/versions"),
  });
}

export interface DraftInput {
  scenarios: ScenarioConfig[];
  note?: string | null;
}

/** POST /conversation-engine/config/check — validate without saving. */
export function useCheckEngineConfig() {
  return useMutation<ConfigCheck, Error, DraftInput>({
    mutationFn: (body) =>
      apiFetch<ConfigCheck>("/conversation-engine/config/check", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  });
}

/** PUT /conversation-engine/config/draft */
export function useSaveEngineDraft() {
  const queryClient = useQueryClient();
  return useMutation<ConfigVersion, Error, DraftInput>({
    mutationFn: (body) =>
      apiFetch<ConfigVersion>("/conversation-engine/config/draft", {
        method: "PUT",
        body: JSON.stringify(body),
      }),
    onSuccess: () => invalidateConfig(queryClient),
  });
}

/** DELETE /conversation-engine/config/draft */
export function useDiscardEngineDraft() {
  const queryClient = useQueryClient();
  return useMutation<unknown, Error, void>({
    mutationFn: () => apiFetch("/conversation-engine/config/draft", { method: "DELETE" }),
    onSuccess: () => invalidateConfig(queryClient),
  });
}

/** POST /conversation-engine/config/draft/publish */
export function usePublishEngineDraft() {
  const queryClient = useQueryClient();
  return useMutation<ConfigVersion, Error, { note?: string | null }>({
    mutationFn: (body) =>
      apiFetch<ConfigVersion>("/conversation-engine/config/draft/publish", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => invalidateConfig(queryClient),
  });
}

/** POST /conversation-engine/config/rollback — publishes a copy of an older version. */
export function useRollbackEngineConfig() {
  const queryClient = useQueryClient();
  return useMutation<ConfigVersion, Error, { version: number; note?: string | null }>({
    mutationFn: (body) =>
      apiFetch<ConfigVersion>("/conversation-engine/config/rollback", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => invalidateConfig(queryClient),
  });
}

/** POST /conversation-engine/simulate — plays one customer turn; changes nothing. */
export function useSimulateTurn() {
  return useMutation<SimulateResult, Error, SimulateInput>({
    mutationFn: (body) =>
      apiFetch<SimulateResult>("/conversation-engine/simulate", {
        method: "POST",
        body: JSON.stringify(body),
      }),
  });
}

/** GET /conversation-engine/audit → who changed what, newest first. */
export function useEngineAudit() {
  return useQuery<EngineAuditEvent[]>({
    queryKey: queryKeys.conversationEngineAudit(),
    queryFn: () => apiFetch<EngineAuditEvent[]>("/conversation-engine/audit?limit=100"),
  });
}

/** GET /conversation-engine/metrics — rollout metrics from the turn log. */
export function useEngineMetrics(filters: { days: number; channel?: "whatsapp" | "voice" }) {
  const params = new URLSearchParams({ days: String(filters.days) });
  if (filters.channel) params.set("channel", filters.channel);
  return useQuery<EngineMetrics>({
    queryKey: queryKeys.conversationEngineMetrics(filters),
    queryFn: () => apiFetch<EngineMetrics>(`/conversation-engine/metrics?${params.toString()}`),
  });
}

/** PATCH /conversation-engine/turns/{id}/review — mark what the engine understood right / wrong. */
export function useReviewTurn() {
  const queryClient = useQueryClient();
  return useMutation<
    ConversationTurn,
    Error,
    { id: string; label: "correct" | "incorrect" | null; correct_scenario?: string | null }
  >({
    mutationFn: ({ id, ...body }) =>
      apiFetch<ConversationTurn>(`/conversation-engine/turns/${id}/review`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["conversation-engine", "turns"] });
      queryClient.invalidateQueries({ queryKey: ["conversation-engine", "metrics"] });
    },
  });
}

/** PUT /conversation-engine/mode — the org's own admin switches Strict / Smart / Human Conversation. */
export function useSetConversationMode() {
  const queryClient = useQueryClient();
  return useMutation<ConversationEngineStatus, Error, ConversationMode>({
    mutationFn: (mode) =>
      apiFetch<ConversationEngineStatus>("/conversation-engine/mode", {
        method: "PUT",
        body: JSON.stringify({ mode }),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.conversationEngineStatus() });
      queryClient.invalidateQueries({ queryKey: queryKeys.conversationEngineAudit() });
    },
  });
}
