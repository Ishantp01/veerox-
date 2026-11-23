import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { Template, TemplateButton } from "@/lib/types";

export interface TemplateFilters {
  active?: boolean;
  /** A WhatsApp number's id (org_numbers row): only the templates of the
   * WABA that number belongs to. Omit for every template of the org. */
  numberId?: string;
}

/** GET /whatsapp-templates[?active=&number_id=] → Template[] */
export function useTemplates(filters?: TemplateFilters, options?: { enabled?: boolean }) {
  const params = new URLSearchParams();
  if (filters?.active !== undefined) params.set("active", String(filters.active));
  if (filters?.numberId) params.set("number_id", filters.numberId);
  const qs = params.toString() ? `?${params.toString()}` : "";
  return useQuery<Template[]>({
    queryKey: queryKeys.templates(filters),
    queryFn: () => apiFetch<Template[]>(`/whatsapp-templates${qs}`),
    enabled: options?.enabled ?? true,
  });
}

export interface TemplateCreateInput {
  name: string;
  language: string;
  category?: string;
  param_labels: string[];
  body_preview?: string;
  header_type?: string;
  header_text?: string;
  header_example?: string;
  /** For an IMAGE/VIDEO/DOCUMENT header: an existing WhatsAppAsset id to
   * upload to Meta for the header's media handle. */
  header_asset_id?: string;
  footer_text?: string;
  buttons?: TemplateButton[];
  active?: boolean;
  /** Submit to the WABA of this WhatsApp number (omit = the org's default WABA). */
  number_id?: string;
}

/** POST /whatsapp-templates → Template */
export function useCreateTemplate() {
  const queryClient = useQueryClient();

  return useMutation<Template, Error, TemplateCreateInput>({
    mutationFn: (body) =>
      apiFetch<Template>("/whatsapp-templates", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["whatsapp-templates"] });
    },
  });
}

export interface TemplateUpdateInput {
  id: string;
  name?: string;
  language?: string;
  category?: string;
  param_labels?: string[];
  body_preview?: string;
  /** For IMAGE/VIDEO/DOCUMENT headers: a default media URL that pre-fills
   * the send form's header field, so it doesn't need retyping every send. */
  header_example?: string;
  active?: boolean;
}

/** PATCH /whatsapp-templates/{id} → Template */
export function useUpdateTemplate() {
  const queryClient = useQueryClient();

  return useMutation<Template, Error, TemplateUpdateInput>({
    mutationFn: ({ id, ...body }) =>
      apiFetch<Template>(`/whatsapp-templates/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["whatsapp-templates"] });
    },
  });
}

/** DELETE /whatsapp-templates/{id} */
export function useDeleteTemplate() {
  const queryClient = useQueryClient();

  return useMutation<{ ok: boolean }, Error, string>({
    mutationFn: (id) =>
      apiFetch<{ ok: boolean }>(`/whatsapp-templates/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["whatsapp-templates"] });
    },
  });
}

export interface TemplateSyncResult {
  created: Template[];
  /** Existing rows that had header/footer/button structure backfilled from
   * Meta (only fills gaps, never overwrites). */
  updated: Template[];
  skipped: number;
  total_on_meta: number;
  /** Local templates of this WABA that Meta no longer has: switched off (not
   * deleted) so a WABA with no templates shows none. */
  deactivated: number;
  waba_id: string | null;
}

/** POST /whatsapp-templates/sync[?number_id=] → adds a local row for every
 * template that exists on Meta for that number's WABA but doesn't have one
 * yet, and switches off the ones Meta no longer has. */
export function useSyncTemplates() {
  const queryClient = useQueryClient();

  return useMutation<TemplateSyncResult, Error, { numberId?: string } | void>({
    mutationFn: (vars) =>
      apiFetch<TemplateSyncResult>(
        `/whatsapp-templates/sync${vars && vars.numberId ? `?number_id=${encodeURIComponent(vars.numberId)}` : ""}`,
        { method: "POST" },
      ),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["whatsapp-templates"] });
    },
  });
}
