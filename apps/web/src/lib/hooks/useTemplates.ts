import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { Template } from "@/lib/types";

export interface TemplateFilters {
  active?: boolean;
}

/** GET /whatsapp-templates → Template[] */
export function useTemplates(filters?: TemplateFilters) {
  const qs = filters?.active !== undefined ? `?active=${filters.active}` : "";
  return useQuery<Template[]>({
    queryKey: queryKeys.templates(filters),
    queryFn: () => apiFetch<Template[]>(`/whatsapp-templates${qs}`),
  });
}

export interface TemplateCreateInput {
  name: string;
  language: string;
  category?: string;
  param_labels: string[];
  body_preview?: string;
  active?: boolean;
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
