import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface TeamMember {
  account_user_id: string;
  email: string;
  full_name: string | null;
  // E.164 mobile number, if this teammate has one on file — the number
  // WhatsApp-notified on a human handoff (see core/tools.py's
  // _resolve_team_notify_phone).
  mobile: string | null;
  role: "admin" | "member";
  is_active: boolean;
  invited_at: string | null;
  joined_at: string | null;
  // The org's original admin — exempt from the plan's max_seats count (see
  // apps/api/routers/team.py's invite_member).
  is_owner: boolean;
}

/** GET /team/members → TeamMember[], scoped to the caller's own org. */
export function useTeamMembers() {
  return useQuery<TeamMember[]>({
    queryKey: ["team", "members"],
    queryFn: () => apiFetch<TeamMember[]>("/team/members"),
  });
}

export interface InviteMemberInput {
  email: string;
  full_name?: string;
  mobile?: string;
  role: string;
}

export interface InviteMemberResult {
  account_user_id: string;
  email: string;
  role: string;
  // Only present when a brand new login was created for this invite — an
  // email that already had an account elsewhere just gets added to this
  // org, no new token issued (see apps/api/routers/team.py).
  login_token: string | null;
}

/** POST /team/members → InviteMemberResult (admin only). */
export function useInviteMember() {
  const queryClient = useQueryClient();
  return useMutation<InviteMemberResult, Error, InviteMemberInput>({
    mutationFn: (body) =>
      apiFetch<InviteMemberResult>("/team/members", {
        method: "POST",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["team", "members"] });
    },
  });
}

export interface UpdateMemberInput {
  accountUserId: string;
  role?: string;
  full_name?: string;
  mobile?: string;
  email?: string;
}

/**
 * PATCH /team/members/{accountUserId} → TeamMember (admin only). Partial —
 * only the fields present on the input are sent, so passing just `{
 * accountUserId, role }` leaves full_name/mobile/email untouched.
 * full_name/mobile/email edit the underlying AccountUser directly, which is
 * shared across every org that person belongs to (see
 * apps/api/schemas/team.py::UpdateMemberIn).
 */
export function useUpdateMember() {
  const queryClient = useQueryClient();
  return useMutation<TeamMember, Error, UpdateMemberInput>({
    mutationFn: ({ accountUserId, ...body }) =>
      apiFetch<TeamMember>(`/team/members/${accountUserId}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["team", "members"] });
    },
  });
}

export interface RegenerateMemberTokenResult {
  account_user_id: string;
  email: string;
  login_token: string;
}

/** POST /team/members/{accountUserId}/regenerate-token → RegenerateMemberTokenResult (admin only). */
export function useRegenerateMemberToken() {
  return useMutation<RegenerateMemberTokenResult, Error, string>({
    mutationFn: (accountUserId) =>
      apiFetch<RegenerateMemberTokenResult>(`/team/members/${accountUserId}/regenerate-token`, {
        method: "POST",
      }),
  });
}

/** DELETE /team/members/{accountUserId} → void (admin only). */
export function useRemoveMember() {
  const queryClient = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (accountUserId) =>
      apiFetch<void>(`/team/members/${accountUserId}`, { method: "DELETE" }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["team", "members"] });
    },
  });
}
