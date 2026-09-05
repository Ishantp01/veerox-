import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, SESSION_TOKEN_KEY } from "@/lib/api";
import { POLL, queryKeys } from "@/lib/query";
import type { Campaign, CampaignCreateResult, CampaignDetail } from "@/lib/types";

/**
 * Campaign list, newest first. Polls every 5s (POLL.campaigns) so progress
 * (pending -> calling -> completed counts) moves live as the background
 * dialer (apps/api/workers/campaign_dialer.py) works through the list.
 *
 * GET /admin/campaigns → Campaign[]
 */
export function useCampaigns(channel?: "voice" | "whatsapp") {
  const qs = channel ? `?channel=${channel}` : "";
  return useQuery<Campaign[]>({
    queryKey: queryKeys.campaigns(channel),
    queryFn: () => apiFetch<Campaign[]>(`/admin/campaigns${qs}`),
    refetchInterval: POLL.campaigns,
  });
}

/**
 * Single campaign plus its full target list. Disabled until `id` is provided.
 *
 * GET /admin/campaigns/{id} → CampaignDetail
 */
export function useCampaign(id: string | undefined | null) {
  return useQuery<CampaignDetail>({
    queryKey: queryKeys.campaign(id ?? ""),
    queryFn: () => apiFetch<CampaignDetail>(`/admin/campaigns/${id}`),
    enabled: Boolean(id),
    refetchInterval: POLL.campaigns,
  });
}

export type CampaignStartMode = "draft" | "now" | "scheduled";

export interface CreateCampaignInput {
  name: string;
  criteria: string;
  file: File;
  /** Omitted on the Campaigns page — channel is resolved per-row from the
   * file's call/whatsapp columns instead. Still supported for callers that
   * want to force every row into one channel. */
  channel?: "voice" | "whatsapp";
  startMode: CampaignStartMode;
  scheduledStartAt?: string;
  templateName?: string;
  templateLanguage?: string;
  /** Ordered per-{{1}}/{{2}}/... values — see template-param-mapper.tsx. */
  templateParams?: string[];
  customMessage?: string;
  /** Voice-only, optional — unset falls back to the org's default script /
   * auto-rotation across its numbers, same as before either field existed. */
  scriptId?: string;
  phoneNumberId?: string;
  /** Voice-only, any integer >= 1 — how many times the dialer re-calls a
   * contact who never picks up before marking them failed. Omitted → backend
   * default 3. */
  maxAttempts?: number;
}

/**
 * Create a campaign from an uploaded CSV/Excel contact list. Plain `fetch`
 * with `FormData` (not `apiFetch`, which always sets a JSON Content-Type) —
 * same reasoning as `importLeadsFile` in leads-view.tsx.
 */
async function createCampaign(input: CreateCampaignInput): Promise<CampaignCreateResult> {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8002";
  const token =
    typeof window === "undefined" ? "" : localStorage.getItem(SESSION_TOKEN_KEY) ?? "";

  const headers: Record<string, string> = {};
  if (token) headers["X-Session-Token"] = token;

  const form = new FormData();
  form.append("name", input.name);
  form.append("criteria", input.criteria);
  form.append("file", input.file);
  if (input.channel) form.append("channel", input.channel);
  form.append("start_mode", input.startMode);
  if (input.scheduledStartAt) form.append("scheduled_start_at", input.scheduledStartAt);
  if (input.templateName) form.append("template_name", input.templateName);
  if (input.templateLanguage) form.append("template_language", input.templateLanguage);
  if (input.templateParams && input.templateParams.length > 0) {
    form.append("template_params", JSON.stringify(input.templateParams));
  }
  if (input.customMessage) form.append("custom_message", input.customMessage);
  if (input.scriptId) form.append("script_id", input.scriptId);
  if (input.phoneNumberId) form.append("phone_number_id", input.phoneNumberId);
  if (input.maxAttempts) form.append("max_attempts", String(input.maxAttempts));

  const res = await fetch(`${base}/admin/campaigns`, {
    method: "POST",
    headers,
    body: form,
  });
  if (!res.ok) {
    let message = `Create campaign failed (${res.status} ${res.statusText})`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") message = body.detail;
    } catch {
      // ignore JSON parse failure — use the status message
    }
    throw new Error(message);
  }
  return res.json();
}

export function useCreateCampaign() {
  const queryClient = useQueryClient();

  return useMutation<CampaignCreateResult, Error, CreateCampaignInput>({
    mutationFn: createCampaign,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
    },
  });
}

function useCampaignStatusMutation(action: "pause" | "resume") {
  const queryClient = useQueryClient();

  return useMutation<{ id: string; status: string }, Error, string>({
    mutationFn: (id: string) =>
      apiFetch<{ id: string; status: string }>(`/admin/campaigns/${id}/${action}`, {
        method: "POST",
      }),
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
      queryClient.invalidateQueries({ queryKey: queryKeys.campaign(id) });
    },
  });
}

export function usePauseCampaign() {
  return useCampaignStatusMutation("pause");
}

export function useResumeCampaign() {
  return useCampaignStatusMutation("resume");
}

/**
 * Change a campaign's voice overrides after creation — most usefully its
 * ``script_id``, which is otherwise pinned forever at creation time and
 * does NOT follow edits made later in the script library (see
 * routers/admin.py's update_campaign). Pass `null` for a field to clear it
 * back to the org-default fallback.
 *
 * PATCH /admin/campaigns/{id} → Campaign
 */
export function useUpdateCampaign() {
  const queryClient = useQueryClient();

  return useMutation<
    Campaign,
    Error,
    { id: string; scriptId?: string | null; phoneNumberId?: string | null; maxAttempts?: number }
  >({
    mutationFn: ({ id, ...body }) =>
      apiFetch<Campaign>(`/admin/campaigns/${id}`, {
        method: "PATCH",
        body: JSON.stringify({
          script_id: body.scriptId,
          phone_number_id: body.phoneNumberId,
          max_attempts: body.maxAttempts,
        }),
      }),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
      queryClient.invalidateQueries({ queryKey: queryKeys.campaign(id) });
    },
  });
}

/** POST /admin/campaigns/{id}/schedule → { id, status } */
export function useScheduleCampaign() {
  const queryClient = useQueryClient();

  return useMutation<{ id: string; status: string }, Error, { id: string; scheduledStartAt: string }>({
    mutationFn: ({ id, scheduledStartAt }) =>
      apiFetch<{ id: string; status: string }>(`/admin/campaigns/${id}/schedule`, {
        method: "POST",
        body: JSON.stringify({ scheduled_start_at: scheduledStartAt }),
      }),
    onSuccess: (_, { id }) => {
      queryClient.invalidateQueries({ queryKey: ["campaigns"] });
      queryClient.invalidateQueries({ queryKey: queryKeys.campaign(id) });
    },
  });
}
