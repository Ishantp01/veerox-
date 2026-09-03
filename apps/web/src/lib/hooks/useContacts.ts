import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { Contact, ContactWithLeads } from "@/lib/types";

/**
 * Contacts list, newest first. Optional `q` substring-filters by name/phone.
 *
 * GET /crm/contacts → Contact[]
 */
export function useContacts(q?: string) {
  const qs = q ? `?q=${encodeURIComponent(q)}` : "";
  return useQuery<Contact[]>({
    queryKey: queryKeys.contacts(q),
    queryFn: () => apiFetch<Contact[]>(`/crm/contacts${qs}`),
  });
}

/**
 * Single contact plus every Lead rolled up under it (cross-channel).
 *
 * GET /crm/contacts/{id} → ContactWithLeads
 */
export function useContact(id: string | undefined | null) {
  return useQuery<ContactWithLeads>({
    queryKey: queryKeys.contact(id ?? ""),
    queryFn: () => apiFetch<ContactWithLeads>(`/crm/contacts/${id}`),
    enabled: Boolean(id),
  });
}

export interface ContactCreateInput {
  name?: string | null;
  phone: string;
  email?: string | null;
  company?: string | null;
  tags?: string[] | null;
}

/** POST /crm/contacts → Contact */
export function useCreateContact() {
  const queryClient = useQueryClient();

  return useMutation<Contact, Error, ContactCreateInput>({
    mutationFn: (body) =>
      apiFetch<Contact>("/crm/contacts", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["contacts"] });
    },
  });
}

export interface ContactUpdateInput {
  name?: string | null;
  phone?: string;
  email?: string | null;
  company?: string | null;
}

/** PATCH /crm/contacts/{id} → Contact. `phone` must stay unique within the
 * org — a conflicting number 409s (see apps/api/routers/crm.py::update_contact). */
export function useUpdateContact() {
  const queryClient = useQueryClient();

  return useMutation<Contact, Error, { id: string } & ContactUpdateInput>({
    mutationFn: ({ id, ...body }) =>
      apiFetch<Contact>(`/crm/contacts/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: (_data, { id }) => {
      queryClient.invalidateQueries({ queryKey: ["contacts"] });
      queryClient.invalidateQueries({ queryKey: queryKeys.contact(id) });
    },
  });
}

/** DELETE /crm/contacts/{id} — leads/appointments keep existing, just lose the contact link. */
export function useDeleteContact() {
  const queryClient = useQueryClient();

  return useMutation<{ ok: boolean }, Error, string>({
    mutationFn: (id) => apiFetch<{ ok: boolean }>(`/crm/contacts/${id}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["contacts"] });
    },
  });
}
