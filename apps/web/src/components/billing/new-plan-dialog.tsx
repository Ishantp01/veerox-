"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import { z } from "zod";
import {
  Button,
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogTitle,
  DialogBody,
  DialogFooter,
  Input,
  Label,
  useToast,
} from "@/components/ui";
import { useCreatePlan } from "@/lib/hooks/useAdminPlans";

const EMPTY = {
  code: "",
  name: "",
  priceRupees: "",
  maxSeats: "",
  maxCampaigns: "",
  maxCallMinutes: "",
  maxWhatsappMessages: "",
  automatedFollowups: false,
};

const planSchema = z.object({
  code: z.string().trim().min(1, "Code is required"),
  name: z.string().trim().min(1, "Name is required"),
  priceRupees: z.coerce.number().nonnegative("Price must be zero or greater"),
  maxSeats: z.coerce.number().nonnegative("Must be zero or greater"),
  maxCampaigns: z.coerce.number().nonnegative("Must be zero or greater"),
  maxCallMinutes: z.coerce.number().nonnegative("Must be zero or greater"),
  maxWhatsappMessages: z.coerce.number().nonnegative("Must be zero or greater"),
});

type PlanFieldErrors = Partial<Record<keyof typeof planSchema.shape, string>>;

export function NewPlanDialog() {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [fieldErrors, setFieldErrors] = useState<PlanFieldErrors>({});
  const createPlan = useCreatePlan();
  const { toast } = useToast();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const parsed = planSchema.safeParse(form);
    if (!parsed.success) {
      const errors: PlanFieldErrors = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0] as keyof PlanFieldErrors;
        if (!errors[key]) errors[key] = issue.message;
      }
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});
    createPlan.mutate(
      {
        code: parsed.data.code,
        name: parsed.data.name,
        price_cents: Math.round(parsed.data.priceRupees * 100),
        limits: {
          max_seats: parsed.data.maxSeats,
          max_campaigns: parsed.data.maxCampaigns,
          max_call_minutes: parsed.data.maxCallMinutes,
          max_whatsapp_messages: parsed.data.maxWhatsappMessages,
          automated_followups: form.automatedFollowups,
        },
      },
      {
        onSuccess: () => {
          toast({ title: "Plan created", variant: "success" });
          setForm(EMPTY);
          setFieldErrors({});
          setOpen(false);
        },
        onError: (err) =>
          toast({ title: "Could not create plan", description: err.message, variant: "error" }),
      }
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger>
        <Button variant="outline" size="sm">
          <Plus size={14} aria-hidden />
          New plan
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>New plan</DialogTitle>
        <form onSubmit={handleSubmit} noValidate>
          <DialogBody className="flex flex-col gap-4">
            <p className="text-[11px] font-bold uppercase tracking-widest text-slate-400 dark:text-slate-500">
              Plan details
            </p>
            <div className="-mt-2 grid grid-cols-2 gap-4">
              <div>
                <Label htmlFor="plan-code">Code *</Label>
                <Input
                  id="plan-code"
                  required
                  value={form.code}
                  onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))}
                  placeholder="enterprise"
                  aria-invalid={fieldErrors.code ? true : undefined}
                  aria-describedby={fieldErrors.code ? "plan-code-error" : undefined}
                />
                {fieldErrors.code && (
                  <p id="plan-code-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.code}
                  </p>
                )}
              </div>
              <div>
                <Label htmlFor="plan-name">Name *</Label>
                <Input
                  id="plan-name"
                  required
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="Enterprise"
                  aria-invalid={fieldErrors.name ? true : undefined}
                  aria-describedby={fieldErrors.name ? "plan-name-error" : undefined}
                />
                {fieldErrors.name && (
                  <p id="plan-name-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.name}
                  </p>
                )}
              </div>
            </div>
            <div>
              <Label htmlFor="plan-price">Price (₹ per renewal)</Label>
              <Input
                id="plan-price"
                type="number"
                min={0}
                value={form.priceRupees}
                onChange={(e) => setForm((f) => ({ ...f, priceRupees: e.target.value }))}
                placeholder="4900"
                aria-invalid={fieldErrors.priceRupees ? true : undefined}
                aria-describedby={fieldErrors.priceRupees ? "plan-price-error" : undefined}
              />
              {fieldErrors.priceRupees && (
                <p id="plan-price-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.priceRupees}
                </p>
              )}
            </div>
            <p className="-mb-1.5 text-[11px] font-bold uppercase tracking-widest text-slate-400 dark:text-slate-500">
              Limits
            </p>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label htmlFor="plan-seats">Team members</Label>
                <Input
                  id="plan-seats"
                  type="number"
                  min={0}
                  value={form.maxSeats}
                  onChange={(e) => setForm((f) => ({ ...f, maxSeats: e.target.value }))}
                  placeholder="5"
                  aria-invalid={fieldErrors.maxSeats ? true : undefined}
                  aria-describedby={fieldErrors.maxSeats ? "plan-seats-error" : undefined}
                />
                {fieldErrors.maxSeats && (
                  <p id="plan-seats-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.maxSeats}
                  </p>
                )}
              </div>
              <div>
                <Label htmlFor="plan-campaigns">Max campaigns</Label>
                <Input
                  id="plan-campaigns"
                  type="number"
                  min={0}
                  value={form.maxCampaigns}
                  onChange={(e) => setForm((f) => ({ ...f, maxCampaigns: e.target.value }))}
                  aria-invalid={fieldErrors.maxCampaigns ? true : undefined}
                  aria-describedby={fieldErrors.maxCampaigns ? "plan-campaigns-error" : undefined}
                />
                {fieldErrors.maxCampaigns && (
                  <p id="plan-campaigns-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.maxCampaigns}
                  </p>
                )}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label htmlFor="plan-call-minutes">Call min / renewal</Label>
                <Input
                  id="plan-call-minutes"
                  type="number"
                  min={0}
                  value={form.maxCallMinutes}
                  onChange={(e) => setForm((f) => ({ ...f, maxCallMinutes: e.target.value }))}
                  aria-invalid={fieldErrors.maxCallMinutes ? true : undefined}
                  aria-describedby={fieldErrors.maxCallMinutes ? "plan-call-minutes-error" : undefined}
                />
                {fieldErrors.maxCallMinutes && (
                  <p id="plan-call-minutes-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.maxCallMinutes}
                  </p>
                )}
              </div>
              <div>
                <Label htmlFor="plan-whatsapp">WhatsApp msgs / renewal</Label>
                <Input
                  id="plan-whatsapp"
                  type="number"
                  min={0}
                  value={form.maxWhatsappMessages}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, maxWhatsappMessages: e.target.value }))
                  }
                  aria-invalid={fieldErrors.maxWhatsappMessages ? true : undefined}
                  aria-describedby={
                    fieldErrors.maxWhatsappMessages ? "plan-whatsapp-error" : undefined
                  }
                />
                {fieldErrors.maxWhatsappMessages && (
                  <p id="plan-whatsapp-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.maxWhatsappMessages}
                  </p>
                )}
              </div>
            </div>
            <label
              htmlFor="plan-automated-followups"
              className="flex items-center gap-2.5 rounded-xl border border-slate-200 bg-slate-50 px-3.5 py-3 text-sm font-medium text-slate-700 dark:border-slate-800 dark:bg-slate-800/50 dark:text-slate-300"
            >
              <input
                id="plan-automated-followups"
                type="checkbox"
                checked={form.automatedFollowups}
                onChange={(e) => setForm((f) => ({ ...f, automatedFollowups: e.target.checked }))}
                className="h-4 w-4 rounded border-slate-300 text-primary-500 dark:border-slate-700"
              />
              Includes automated follow-ups
            </label>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={createPlan.isPending}>
              Create plan
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
