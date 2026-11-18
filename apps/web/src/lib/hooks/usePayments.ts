import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/api";

export interface PaymentPlan {
  id: string;
  code: string;
  name: string;
  description: string | null;
  price_paise: number;
  currency: string;
  duration_days: number;
  is_active: boolean;
  // Add-on pack: its limits are EXTRA amounts on top of the base plan
  is_addon: boolean;
  // null = unlimited
  max_call_minutes: number | null;
  max_whatsapp_messages: number | null;
  max_team_members: number | null;
  max_voice_campaigns: number | null;
  max_whatsapp_campaigns: number | null;
}

export type LimitKey =
  | "max_call_minutes"
  | "max_whatsapp_messages"
  | "max_team_members"
  | "max_voice_campaigns"
  | "max_whatsapp_campaigns";

export const LIMIT_FIELDS: { key: LimitKey; resource: string; label: string; unit: string }[] = [
  { key: "max_call_minutes", resource: "call_minutes", label: "Call minutes", unit: "min" },
  { key: "max_whatsapp_messages", resource: "whatsapp_messages", label: "WhatsApp messages", unit: "msgs" },
  { key: "max_team_members", resource: "team_members", label: "Team members", unit: "members" },
  { key: "max_voice_campaigns", resource: "voice_campaigns", label: "Voice campaigns", unit: "campaigns" },
  { key: "max_whatsapp_campaigns", resource: "whatsapp_campaigns", label: "WhatsApp campaigns", unit: "campaigns" },
];

export interface UsageItem {
  resource: string;
  limit: number | null;
  used: number;
}

export const usePlanUsage = () =>
  useQuery<UsageItem[]>({
    queryKey: ["payments", "usage"],
    queryFn: () => apiFetch("/payments/usage"),
    // Always fresh: limits are enforced server-side the moment they are hit.
    staleTime: 0,
    refetchOnMount: "always",
    refetchInterval: 15_000,
  });

export interface PaymentRecord {
  id: string;
  plan_id: string;
  razorpay_order_id: string;
  razorpay_payment_id: string | null;
  amount_paise: number;
  currency: string;
  status: "created" | "paid" | "failed";
  period_start: string | null;
  period_end: string | null;
  created_at: string;
}

export interface PaymentStatus {
  active: boolean;
  plan_required: boolean;
  plan: PaymentPlan | null;
  period_end: string | null;
}

export interface CheckoutSession {
  key_id: string;
  order_id: string;
  amount: number;
  currency: string;
  plan_name: string;
}

export interface VerifyInput {
  razorpay_order_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
}

export const usePaymentPlans = () =>
  useQuery<PaymentPlan[]>({ queryKey: ["payments", "plans"], queryFn: () => apiFetch("/payments/plans") });

export const usePaymentAddons = (enabled = true) =>
  useQuery<PaymentPlan[]>({
    queryKey: ["payments", "addons"],
    queryFn: () => apiFetch("/payments/addons"),
    enabled,
  });

export const usePaymentStatus = (enabled = true) =>
  useQuery<PaymentStatus>({
    queryKey: ["payments", "status"],
    queryFn: () => apiFetch("/payments/status"),
    enabled,
    // Bounded: PlanGate shows a loader while this runs, so don't let default retries stretch it.
    retry: 1,
  });

export const usePaymentHistory = () =>
  useQuery<PaymentRecord[]>({ queryKey: ["payments", "history"], queryFn: () => apiFetch("/payments/history") });

export function useCreateCheckout() {
  return useMutation<CheckoutSession, Error, string>({
    mutationFn: (plan_id) =>
      apiFetch("/payments/checkout", { method: "POST", body: JSON.stringify({ plan_id }) }),
  });
}

export function useVerifyPayment() {
  const qc = useQueryClient();
  return useMutation<PaymentRecord, Error, VerifyInput>({
    mutationFn: (input) =>
      apiFetch("/payments/verify", { method: "POST", body: JSON.stringify(input) }),
    onSuccess: () => {
      // The payment is verified: open the gate right now instead of waiting
      // for a slow status refetch, then refresh everything in the background.
      qc.setQueryData<PaymentStatus>(["payments", "status"], (old) => ({
        plan: null,
        period_end: null,
        ...old,
        active: true,
        plan_required: false,
      }));
      qc.invalidateQueries({ queryKey: ["payments"] });
    },
  });
}

export function formatPrice(paise: number, currency: string): string {
  return new Intl.NumberFormat("en-IN", { style: "currency", currency }).format(paise / 100);
}

// ── Platform-owner plan catalog (superuser only) ─────────────────────────

export interface PlanInput {
  code: string;
  name: string;
  description: string | null;
  price_paise: number;
  duration_days: number;
  is_active: boolean;
  is_addon?: boolean;
  max_call_minutes: number | null;
  max_whatsapp_messages: number | null;
  max_team_members: number | null;
  max_voice_campaigns: number | null;
  max_whatsapp_campaigns: number | null;
}

export const useAdminPaymentPlans = () =>
  useQuery<PaymentPlan[]>({
    queryKey: ["payments", "admin", "plans"],
    queryFn: () => apiFetch("/payments/admin/plans"),
  });

export function useCreatePaymentPlan() {
  const qc = useQueryClient();
  return useMutation<PaymentPlan, Error, PlanInput>({
    mutationFn: (input) =>
      apiFetch("/payments/admin/plans", { method: "POST", body: JSON.stringify(input) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["payments"] }),
  });
}

export function useDeletePaymentPlan() {
  const qc = useQueryClient();
  return useMutation<{ ok: boolean }, Error, string>({
    mutationFn: (id) => apiFetch(`/payments/admin/plans/${id}`, { method: "DELETE" }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["payments"] }),
  });
}

export function useUpdatePaymentPlan() {
  const qc = useQueryClient();
  return useMutation<PaymentPlan, Error, { id: string; patch: Partial<Omit<PlanInput, "code">> }>({
    mutationFn: ({ id, patch }) =>
      apiFetch(`/payments/admin/plans/${id}`, { method: "PATCH", body: JSON.stringify(patch) }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["payments"] }),
  });
}
