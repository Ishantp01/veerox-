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
  Select,
  useToast,
} from "@/components/ui";
import { PLAN_RESOURCE_TYPE_OPTIONS, useCreatePlan } from "@/lib/hooks/useAdminPlans";

const EMPTY = {
  code: "",
  name: "",
  priceRupees: "",
  maxSeats: "",
  maxCampaigns: "",
  maxCallMinutes: "",
  maxWhatsappMessages: "",
  automatedFollowups: false,
  resourceType: "",
};

// Blank means "not included in this plan" (stored as 0, same convention as
// explicitly typing 0 — see choose-plan-cards.tsx, which hides a 0-valued
// limit from the feature list entirely). Left blank is just friendlier than
// forcing every admin to type "0" for a resource a plan doesn't grant.
const limitField = z
  .string()
  .trim()
  .transform((v) => (v === "" ? 0 : Number(v)))
  .pipe(z.number().nonnegative("Must be zero or greater"));

const planFields = z.object({
  code: z.string().trim().min(1, "Code is required"),
  name: z.string().trim().min(1, "Name is required"),
  priceRupees: z.coerce.number().nonnegative("Price must be zero or greater"),
  maxSeats: limitField,
  maxCampaigns: limitField,
  maxCallMinutes: limitField,
  maxWhatsappMessages: limitField,
});

const planSchema = planFields.refine(
  (data) =>
    [data.maxSeats, data.maxCampaigns, data.maxCallMinutes, data.maxWhatsappMessages].some(
      (v) => v > 0
    ),
  { message: "Set at least one limit above zero — a plan with nothing in it can't be purchased." }
);

type PlanFieldErrors = Partial<Record<keyof typeof planFields.shape, string>>;

export function NewPlanDialog() {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [fieldErrors, setFieldErrors] = useState<PlanFieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const createPlan = useCreatePlan();
  const { toast } = useToast();

  const showAllLimits = form.resourceType === "";
  const showSeats = showAllLimits || form.resourceType === "max_team_members";
  const showCampaigns = showAllLimits || form.resourceType === "max_campaigns";
  const showCallMinutes = showAllLimits || form.resourceType === "max_call_minutes";
  const showWhatsapp = showAllLimits || form.resourceType === "max_whatsapp_messages";

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const parsed = planSchema.safeParse(form);
    if (!parsed.success) {
      const errors: PlanFieldErrors = {};
      let topLevelError: string | null = null;
      for (const issue of parsed.error.issues) {
        if (issue.path.length === 0) {
          topLevelError = issue.message;
          continue;
        }
        const key = issue.path[0] as keyof PlanFieldErrors;
        if (!errors[key]) errors[key] = issue.message;
      }
      setFieldErrors(errors);
      setFormError(topLevelError);
      return;
    }
    setFieldErrors({});
    setFormError(null);
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
        resource_type: form.resourceType || null,
      },
      {
        onSuccess: () => {
          toast({ title: "Plan created", variant: "success" });
          setForm(EMPTY);
          setFieldErrors({});
          setFormError(null);
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
            <div>
              <Label htmlFor="plan-resource-type">Plan type</Label>
              <Select
                id="plan-resource-type"
                value={form.resourceType}
                onChange={(value) => setForm((f) => ({ ...f, resourceType: value }))}
                className="w-full"
              >
                {PLAN_RESOURCE_TYPE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </Select>
              <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
                {form.resourceType
                  ? "A recharge SKU: buying it only tops up this one resource — the other limits below are ignored."
                  : "A full plan: buying it replaces every resource below with the values you set here."}
              </p>
            </div>
            <div>
              <p className="text-[11px] font-bold uppercase tracking-widest text-slate-400 dark:text-slate-500">
                Limits
              </p>
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                Leave a limit blank for &quot;not included&quot; — at least one must be set.
              </p>
            </div>
            {formError && (
              <p role="alert" className="-mt-2 text-xs text-red-600">
                {formError}
              </p>
            )}
            {(showSeats || showCampaigns) && (
              <div className="grid grid-cols-2 gap-4">
                {showSeats && (
                  <div className={showCampaigns ? undefined : "col-span-2"}>
                    <Label htmlFor="plan-seats">Team members</Label>
                    <Input
                      id="plan-seats"
                      type="number"
                      min={0}
                      value={form.maxSeats}
                      onChange={(e) => setForm((f) => ({ ...f, maxSeats: e.target.value }))}
                      placeholder="Not included"
                      aria-invalid={fieldErrors.maxSeats ? true : undefined}
                      aria-describedby={fieldErrors.maxSeats ? "plan-seats-error" : undefined}
                    />
                    {fieldErrors.maxSeats && (
                      <p id="plan-seats-error" className="mt-1.5 text-xs text-red-600">
                        {fieldErrors.maxSeats}
                      </p>
                    )}
                  </div>
                )}
                {showCampaigns && (
                  <div className={showSeats ? undefined : "col-span-2"}>
                    <Label htmlFor="plan-campaigns">Max campaigns</Label>
                    <Input
                      id="plan-campaigns"
                      type="number"
                      min={0}
                      value={form.maxCampaigns}
                      onChange={(e) => setForm((f) => ({ ...f, maxCampaigns: e.target.value }))}
                      placeholder="Not included"
                      aria-invalid={fieldErrors.maxCampaigns ? true : undefined}
                      aria-describedby={fieldErrors.maxCampaigns ? "plan-campaigns-error" : undefined}
                    />
                    {fieldErrors.maxCampaigns && (
                      <p id="plan-campaigns-error" className="mt-1.5 text-xs text-red-600">
                        {fieldErrors.maxCampaigns}
                      </p>
                    )}
                  </div>
                )}
              </div>
            )}
            {(showCallMinutes || showWhatsapp) && (
              <div className="grid grid-cols-2 gap-4">
                {showCallMinutes && (
                  <div className={showWhatsapp ? undefined : "col-span-2"}>
                    <Label htmlFor="plan-call-minutes">Call min / renewal</Label>
                    <Input
                      id="plan-call-minutes"
                      type="number"
                      min={0}
                      value={form.maxCallMinutes}
                      onChange={(e) => setForm((f) => ({ ...f, maxCallMinutes: e.target.value }))}
                      placeholder="Not included"
                      aria-invalid={fieldErrors.maxCallMinutes ? true : undefined}
                      aria-describedby={fieldErrors.maxCallMinutes ? "plan-call-minutes-error" : undefined}
                    />
                    {fieldErrors.maxCallMinutes && (
                      <p id="plan-call-minutes-error" className="mt-1.5 text-xs text-red-600">
                        {fieldErrors.maxCallMinutes}
                      </p>
                    )}
                  </div>
                )}
                {showWhatsapp && (
                  <div className={showCallMinutes ? undefined : "col-span-2"}>
                    <Label htmlFor="plan-whatsapp">WhatsApp msgs / renewal</Label>
                    <Input
                      id="plan-whatsapp"
                      type="number"
                      min={0}
                      value={form.maxWhatsappMessages}
                      onChange={(e) =>
                        setForm((f) => ({ ...f, maxWhatsappMessages: e.target.value }))
                      }
                      placeholder="Not included"
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
                )}
              </div>
            )}
            {showAllLimits && (
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
            )}
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
