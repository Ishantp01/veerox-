import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch, SESSION_TOKEN_KEY } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { WhatsAppAsset } from "@/lib/types";

/**
 * This org's WhatsApp media library — files the AI agent can send to a
 * contact who asks for that information (a price list PDF, brochure image,
 * short video). See apps/api/core/tools.py's send_whatsapp_file.
 *
 * GET /admin/whatsapp-assets → WhatsAppAsset[]
 */
export function useWhatsappAssets() {
  return useQuery<WhatsAppAsset[]>({
    queryKey: queryKeys.whatsappAssets(),
    queryFn: () => apiFetch<WhatsAppAsset[]>("/admin/whatsapp-assets"),
  });
}

export interface UploadWhatsappAssetInput {
  name: string;
  description?: string;
  file: File;
}

/**
 * Upload a file into the library. Plain `fetch` + `FormData` (not `apiFetch`,
 * which forces a JSON Content-Type) — same reasoning as `useCampaigns.ts`.
 *
 * POST /admin/whatsapp-assets (multipart) → WhatsAppAsset
 */
async function uploadWhatsappAsset(input: UploadWhatsappAssetInput): Promise<WhatsAppAsset> {
  const base = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8002";
  const token =
    typeof window === "undefined" ? "" : localStorage.getItem(SESSION_TOKEN_KEY) ?? "";

  const headers: Record<string, string> = {};
  if (token) headers["X-Session-Token"] = token;

  const form = new FormData();
  form.append("name", input.name);
  if (input.description) form.append("description", input.description);
  form.append("file", input.file);

  const res = await fetch(`${base}/admin/whatsapp-assets`, {
    method: "POST",
    headers,
    body: form,
  });
  if (!res.ok) {
    let message = `Upload failed (${res.status} ${res.statusText})`;
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

export function useUploadWhatsappAsset() {
  const queryClient = useQueryClient();

  return useMutation<WhatsAppAsset, Error, UploadWhatsappAssetInput>({
    mutationFn: uploadWhatsappAsset,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.whatsappAssets() });
    },
  });
}

export interface UpdateWhatsappAssetInput {
  id: string;
  name?: string;
  description?: string | null;
}

/** PATCH /admin/whatsapp-assets/{id} → WhatsAppAsset */
export function useUpdateWhatsappAsset() {
  const queryClient = useQueryClient();

  return useMutation<WhatsAppAsset, Error, UpdateWhatsappAssetInput>({
    mutationFn: ({ id, ...body }) =>
      apiFetch<WhatsAppAsset>(`/admin/whatsapp-assets/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.whatsappAssets() });
    },
  });
}

/** DELETE /admin/whatsapp-assets/{id} */
export function useDeleteWhatsappAsset() {
  const queryClient = useQueryClient();

  return useMutation<{ ok: boolean }, Error, string>({
    mutationFn: (id) =>
      apiFetch<{ ok: boolean }>(`/admin/whatsapp-assets/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: queryKeys.whatsappAssets() });
    },
  });
}
