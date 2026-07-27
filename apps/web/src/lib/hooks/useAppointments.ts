import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiFetch } from "@/lib/api";
import { queryKeys } from "@/lib/query";
import type { Appointment, AppointmentStatus } from "@/lib/types";

export interface AppointmentFilters {
  status?: AppointmentStatus;
}

function buildAppointmentsPath(filters?: AppointmentFilters): string {
  const params = new URLSearchParams();
  if (filters?.status) params.set("status", filters.status);
  const qs = params.toString();
  return qs ? `/appointments?${qs}` : "/appointments";
}

/** Upcoming-first appointments list. GET /appointments → Appointment[] */
export function useAppointments(filters?: AppointmentFilters) {
  return useQuery<Appointment[]>({
    queryKey: queryKeys.appointments(filters),
    queryFn: () => apiFetch<Appointment[]>(buildAppointmentsPath(filters)),
  });
}

export interface AppointmentCreateInput {
  contact_id?: string | null;
  lead_id?: string | null;
  scheduled_at: string;
  duration_minutes?: number;
  notes?: string | null;
}

/** POST /appointments → Appointment */
export function useCreateAppointment() {
  const queryClient = useQueryClient();

  return useMutation<Appointment, Error, AppointmentCreateInput>({
    mutationFn: (body) =>
      apiFetch<Appointment>("/appointments", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["appointments"] });
    },
  });
}

export interface AppointmentUpdateInput {
  id: string;
  status?: AppointmentStatus;
  scheduled_at?: string;
  notes?: string | null;
}

/** PATCH /appointments/{id} → Appointment */
export function useUpdateAppointment() {
  const queryClient = useQueryClient();

  return useMutation<Appointment, Error, AppointmentUpdateInput>({
    mutationFn: ({ id, ...body }) =>
      apiFetch<Appointment>(`/appointments/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["appointments"] });
    },
  });
}
