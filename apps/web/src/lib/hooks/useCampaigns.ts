import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { POLL, queryKeys } from "@/lib/query";
import type { Campaign, CampaignCreateResult, CampaignDetail } from "@/lib/types";

const TOKEN_KEY = "veerox_admin_token";

/**
 * Campaign list, newest first. Polls every 5s (POLL.campaigns) so progress
 * (pending -> calling -> completed counts) moves live as the background
 * dialer (apps/api/workers/campaign_dialer.py) works through the list.
 *
 * GET /admin/campaigns → Campaign[]
 */
export function useCampaigns() {
  return useQuery<Campaign[]>({
    queryKey: queryKeys.campaigns(),
    queryFn: () => apiFetch<Campaign[]>("/admin/campaigns"),
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

export interface CreateCampaignInput {
  name: string;
  criteria: string;
  file: File;
}

/**
 * Create a campaign from an uploaded CSV/Excel contact list. Plain `fetch`
 * with `FormData` (not `apiFetch`, which always sets a JSON Content-Type) —
 * same reasoning as `importLeadsFile` in leads-view.tsx.
 */
async function createCampaign(input: CreateCampaignInput): Promise<CampaignCreateResult> {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8001";
  const token = typeof window === "undefined" ? "" : localStorage.getItem(TOKEN_KEY) ?? "";

  const headers: Record<string, string> = {};
  if (token) headers["X-Admin-Token"] = token;

  const form = new FormData();
  form.append("name", input.name);
  form.append("criteria", input.criteria);
  form.append("file", input.file);

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
      queryClient.invalidateQueries({ queryKey: queryKeys.campaigns() });
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
      queryClient.invalidateQueries({ queryKey: queryKeys.campaigns() });
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
