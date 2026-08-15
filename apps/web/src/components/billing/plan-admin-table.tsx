"use client";

import { Trash2 } from "lucide-react";
import {
  Badge,
  Button,
  Table,
  TableCell,
  TableHeader,
  TableRow,
  useToast,
} from "@/components/ui";
import { useAdminPlans, useDeletePlan, useUpdatePlan, type AdminPlan } from "@/lib/hooks/useAdminPlans";
import { NewPlanDialog } from "./new-plan-dialog";

function formatRupees(cents: number): string {
  return cents === 0 ? "Free" : `₹${(cents / 100).toLocaleString("en-IN")}`;
}

function LimitCell({ plan, field }: { plan: AdminPlan; field: string }) {
  const updatePlan = useUpdatePlan();
  const { toast } = useToast();
  const value = Number(plan.limits[field] ?? 0);

  function handleBlur(e: React.FocusEvent<HTMLInputElement>) {
    const next = Number(e.target.value);
    if (next === value) return;
    updatePlan.mutate(
      { code: plan.code, limits: { ...plan.limits, [field]: next } },
      {
        onError: (err) =>
          toast({ title: "Could not update limit", description: err.message, variant: "error" }),
      }
    );
  }

  return (
    <input
      type="number"
      min={0}
      defaultValue={value}
      onBlur={handleBlur}
      className="w-24 rounded-lg border border-slate-300 bg-white px-2 py-1 text-sm dark:border-slate-700 dark:bg-slate-900"
    />
  );
}

function BooleanLimitCell({ plan, field }: { plan: AdminPlan; field: string }) {
  const updatePlan = useUpdatePlan();
  const { toast } = useToast();
  const checked = plan.limits[field] === true;

  function handleChange(e: React.ChangeEvent<HTMLInputElement>) {
    updatePlan.mutate(
      { code: plan.code, limits: { ...plan.limits, [field]: e.target.checked } },
      {
        onError: (err) =>
          toast({ title: "Could not update limit", description: err.message, variant: "error" }),
      }
    );
  }

  return (
    <input
      type="checkbox"
      checked={checked}
      onChange={handleChange}
      className="h-4 w-4 rounded border-slate-300 text-primary-500 dark:border-slate-700"
      aria-label={`${field} for ${plan.name}`}
    />
  );
}

/**
 * Platform-wide plan catalog editor — every field here affects billing/limits
 * for all orgs on that plan, not just the viewer's own org (see
 * apps/api/routers/billing.py's PlatformAdminDep comment on why this is
 * gated by admin-token/session rather than org role).
 */
export function PlanAdminTable() {
  const { data, isLoading } = useAdminPlans();
  const deletePlan = useDeletePlan();
  const { toast } = useToast();
  const plans = data ?? [];

  function handleDelete(code: string) {
    if (!window.confirm(`Delete plan "${code}"? This only works if no org is on it.`)) return;
    deletePlan.mutate(code, {
      onSuccess: () => toast({ title: "Plan deleted", variant: "success" }),
      onError: (err) =>
        toast({ title: "Could not delete plan", description: err.message, variant: "error" }),
    });
  }

  if (isLoading) return null;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-700 dark:text-slate-300">Plan catalog</h3>
        <NewPlanDialog />
      </div>
      <div className="overflow-x-auto rounded-2xl border border-slate-200/80 bg-white shadow-card dark:border-slate-800 dark:bg-slate-900">
        <Table>
          <thead>
            <TableRow isHeader>
              <TableHeader>Plan</TableHeader>
              <TableHeader>Price / renewal</TableHeader>
              <TableHeader>Team members</TableHeader>
              <TableHeader>Campaigns</TableHeader>
              <TableHeader>Call min / renewal</TableHeader>
              <TableHeader>WhatsApp msgs / renewal</TableHeader>
              <TableHeader>Automated follow-ups</TableHeader>
              <TableHeader className="text-right">Actions</TableHeader>
            </TableRow>
          </thead>
          <tbody>
            {plans.map((plan) => (
              <TableRow key={plan.code}>
                <TableCell className="font-medium text-slate-900 dark:text-slate-100">
                  {plan.name}
                  {!plan.is_active && (
                    <Badge variant="neutral" className="ml-2">
                      inactive
                    </Badge>
                  )}
                </TableCell>
                <TableCell>{formatRupees(plan.price_cents)}</TableCell>
                <TableCell>
                  <LimitCell plan={plan} field="max_seats" />
                </TableCell>
                <TableCell>
                  <LimitCell plan={plan} field="max_campaigns" />
                </TableCell>
                <TableCell>
                  <LimitCell plan={plan} field="max_call_minutes" />
                </TableCell>
                <TableCell>
                  <LimitCell plan={plan} field="max_whatsapp_messages" />
                </TableCell>
                <TableCell>
                  <BooleanLimitCell plan={plan} field="automated_followups" />
                </TableCell>
                <TableCell className="text-right">
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => handleDelete(plan.code)}
                    aria-label={`Delete ${plan.name}`}
                  >
                    <Trash2 size={14} />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </tbody>
        </Table>
      </div>
    </div>
  );
}
