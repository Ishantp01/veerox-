"use client";

import { useState } from "react";
import { Check, CreditCard, Pencil, Plus } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Badge,
  Button,
  Card,
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogTitle,
  EmptyState,
  Input,
  Label,
  Textarea,
  useToast,
} from "@/components/ui";
import {
  formatPrice,
  LIMIT_FIELDS,
  type LimitKey,
  type PaymentPlan,
  useAdminPaymentPlans,
  useCreatePaymentPlan,
  useUpdatePaymentPlan,
} from "@/lib/hooks/usePayments";

interface FormState {
  code: string;
  name: string;
  description: string;
  price: string; // rupees, as typed
  duration_days: string;
  limits: Record<LimitKey, string>; // blank = unlimited
}

const EMPTY_LIMITS = Object.fromEntries(LIMIT_FIELDS.map((f) => [f.key, ""])) as Record<LimitKey, string>;
const EMPTY: FormState = { code: "", name: "", description: "", price: "", duration_days: "30", limits: EMPTY_LIMITS };

function PlanDialog({
  plan,
  open,
  onClose,
  addon = false,
}: {
  plan: PaymentPlan | null; // null = create
  open: boolean;
  onClose: () => void;
  addon?: boolean; // creating an add-on pack
}) {
  const isAddon = plan ? plan.is_addon : addon;
  const { toast } = useToast();
  const create = useCreatePaymentPlan();
  const update = useUpdatePaymentPlan();
  const [form, setForm] = useState<FormState>(
    plan
      ? {
          code: plan.code,
          name: plan.name,
          description: plan.description ?? "",
          price: String(plan.price_paise / 100),
          duration_days: String(plan.duration_days),
          limits: Object.fromEntries(
            LIMIT_FIELDS.map((f) => [f.key, plan[f.key] === null ? "" : String(plan[f.key])]),
          ) as Record<LimitKey, string>,
        }
      : EMPTY,
  );
  const set = (k: keyof FormState) => (e: { target: { value: string } }) =>
    setForm((f) => ({ ...f, [k]: e.target.value }));

  const price = Math.round(parseFloat(form.price) * 100);
  const days = parseInt(form.duration_days, 10);
  const parsedLimits = Object.fromEntries(
    LIMIT_FIELDS.map((f) => {
      const raw = form.limits[f.key].trim();
      return [f.key, raw === "" ? null : parseInt(raw, 10)];
    }),
  ) as Record<LimitKey, number | null>;
  const limitsValid = Object.values(parsedLimits).every((v) => v === null || (Number.isFinite(v) && v >= 0));
  const valid = limitsValid &&
    form.name.trim() && (plan || form.code.trim()) && price > 0 && Number.isFinite(price) && days > 0;
  const busy = create.isPending || update.isPending;

  function submit() {
    const fail = (err: Error) => toast({ title: "Could not save plan", description: err.message, variant: "error" });
    const done = () => {
      toast({ title: plan ? "Plan updated" : "Plan created", variant: "success" });
      onClose();
    };
    const body = {
      name: form.name.trim(),
      description: form.description.trim() || null,
      price_paise: price,
      duration_days: days,
      ...parsedLimits,
    };
    if (plan) update.mutate({ id: plan.id, patch: body }, { onSuccess: done, onError: fail });
    else
      create.mutate(
        { ...body, code: form.code.trim().toLowerCase(), is_active: true, is_addon: isAddon },
        { onSuccess: done, onError: fail },
      );
  }

  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-xl">
        <DialogTitle>{plan ? `Edit ${plan.name}` : isAddon ? "New add-on pack" : "New plan"}</DialogTitle>
        <DialogBody className="flex flex-col gap-4">
          <div>
            <Label>Name</Label>
            <Input value={form.name} onChange={set("name")} placeholder="e.g. Pro" />
          </div>
          {!plan && (
            <div>
              <Label>Code</Label>
              <Input value={form.code} onChange={set("code")} placeholder="e.g. pro-monthly (cannot change later)" />
            </div>
          )}
          <div className="grid grid-cols-2 gap-4">
            <div>
              <Label>Price (₹)</Label>
              <Input type="number" min="1" step="0.01" value={form.price} onChange={set("price")} placeholder="999" />
            </div>
            <div>
              <Label>Duration (days)</Label>
              <Input type="number" min="1" value={form.duration_days} onChange={set("duration_days")} />
            </div>
          </div>
          <div>
            <Label>{isAddon ? "Extra allowance" : "Usage limits"}</Label>
            <p className="mb-2 text-xs text-slate-500 dark:text-slate-400">
              {isAddon
                ? "Added on top of the customer's current plan until its period ends. Fill only what this pack adds (e.g. call minutes); leave the rest blank."
                : "Applied while the plan is active. Leave blank for unlimited. The owner org is never limited."}
            </p>
            <div className="grid grid-cols-2 gap-3">
              {LIMIT_FIELDS.map((f) => (
                <div key={f.key}>
                  <label className="mb-1 block text-xs font-medium text-slate-600 dark:text-slate-300">{f.label}</label>
                  <Input
                    type="number"
                    min="0"
                    placeholder={isAddon ? "None" : "Unlimited"}
                    value={form.limits[f.key]}
                    onChange={(e) => setForm((s) => ({ ...s, limits: { ...s.limits, [f.key]: e.target.value } }))}
                  />
                </div>
              ))}
            </div>
          </div>
          <div>
            <Label>Description</Label>
            <Textarea rows={3} value={form.description} onChange={set("description")} placeholder="What this plan includes" />
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button disabled={!valid || busy} onClick={submit}>
            {busy ? "Saving…" : plan ? "Save changes" : isAddon ? "Create add-on" : "Create plan"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

/** Owner-org view: create and manage the plan catalog. The owner never buys. */
export function PlanManager() {
  const { toast } = useToast();
  const plans = useAdminPaymentPlans();
  const update = useUpdatePaymentPlan();
  const [editing, setEditing] = useState<PaymentPlan | null>(null);
  const [creating, setCreating] = useState(false);
  const [creatingAddon, setCreatingAddon] = useState(false);

  function toggle(plan: PaymentPlan) {
    update.mutate(
      { id: plan.id, patch: { is_active: !plan.is_active } },
      {
        onError: (err) => toast({ title: "Could not update plan", description: err.message, variant: "error" }),
      },
    );
  }

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader
        title="Plans"
        description="Create the plans customer organizations can buy. Only active plans are shown to them."
        action={
          <div className="flex gap-2">
            <Button variant="outline" onClick={() => setCreatingAddon(true)}>
              <Plus size={16} aria-hidden /> New add-on
            </Button>
            <Button onClick={() => setCreating(true)}>
              <Plus size={16} aria-hidden /> New plan
            </Button>
          </div>
        }
      />

      <QueryBoundary
        isLoading={plans.isLoading}
        isError={plans.isError}
        error={plans.error}
        onRetry={() => plans.refetch()}
        isEmpty={plans.data?.length === 0}
        emptyFallback={
          <EmptyState
            icon={CreditCard}
            title="No plans yet"
            description="Create your first plan so customers can start paying."
          />
        }
      >
        <div data-tour="plan-catalog" className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {plans.data?.map((p) => (
            <Card
              key={p.id}
              className={"flex flex-col p-6 transition-shadow hover:shadow-md " + (p.is_active ? "" : "opacity-70")}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <h2 className="truncate text-base font-semibold text-slate-900 dark:text-slate-50">{p.name}</h2>
                  <p className="text-xs text-slate-500 dark:text-slate-400">{p.code}</p>
                </div>
                <div className="flex gap-1.5">
                  {p.is_addon && <Badge variant="neutral">Add-on</Badge>}
                  <Badge variant={p.is_active ? "success" : "neutral"}>{p.is_active ? "Active" : "Hidden"}</Badge>
                </div>
              </div>

              <p className="mt-4 flex items-baseline gap-1.5">
                <span className="text-4xl font-extrabold tracking-tight text-slate-900 dark:text-slate-50">
                  {formatPrice(p.price_paise, p.currency)}
                </span>
                <span className="text-sm text-slate-500 dark:text-slate-400">/ {p.duration_days} days</span>
              </p>
              {p.description && <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">{p.description}</p>}

              <ul className="mt-5 flex-1 space-y-2 text-sm text-slate-600 dark:text-slate-300">
                {LIMIT_FIELDS.map((f) => (
                  <li key={f.key} className="flex gap-2">
                    <Check size={16} className="mt-0.5 shrink-0 text-emerald-500" aria-hidden />
                    <span>
                      {p.is_addon
                        ? `+${(p[f.key] ?? 0).toLocaleString("en-IN")}`
                        : p[f.key] === null
                          ? "Unlimited"
                          : p[f.key]!.toLocaleString("en-IN")}{" "}
                      {f.label.toLowerCase()}
                    </span>
                  </li>
                ))}
              </ul>

              <div className="mt-6 flex gap-2">
                <Button variant="outline" className="flex-1" onClick={() => setEditing(p)}>
                  <Pencil size={14} aria-hidden /> Edit
                </Button>
                <Button variant="ghost" disabled={update.isPending} onClick={() => toggle(p)}>
                  {p.is_active ? "Hide" : "Activate"}
                </Button>
              </div>
            </Card>
          ))}
          <button
            type="button"
            onClick={() => setCreating(true)}
            className="flex min-h-[16rem] flex-col items-center justify-center gap-2 rounded-xl border-2 border-dashed border-slate-300 text-sm font-medium text-slate-500 transition-colors hover:border-primary-500 hover:text-primary-600 dark:border-slate-700 dark:text-slate-400 dark:hover:border-primary-400 dark:hover:text-primary-400"
          >
            <Plus size={22} aria-hidden /> New plan
          </button>
        </div>
      </QueryBoundary>

      {/* key remounts the form so each open starts from the right plan's values */}
      {creating && <PlanDialog key="new" plan={null} open onClose={() => setCreating(false)} />}
      {creatingAddon && <PlanDialog key="new-addon" plan={null} addon open onClose={() => setCreatingAddon(false)} />}
      {editing && <PlanDialog key={editing.id} plan={editing} open onClose={() => setEditing(null)} />}
    </div>
  );
}
