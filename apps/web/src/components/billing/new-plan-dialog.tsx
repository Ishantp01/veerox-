"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
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
  maxCallMinutesPerMonth: "",
  maxWhatsappMessagesPerMonth: "",
  automatedFollowups: false,
};

export function NewPlanDialog() {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const createPlan = useCreatePlan();
  const { toast } = useToast();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    createPlan.mutate(
      {
        code: form.code.trim(),
        name: form.name.trim(),
        price_cents_monthly: Math.round(Number(form.priceRupees || 0) * 100),
        limits: {
          max_seats: Number(form.maxSeats || 0),
          max_campaigns: Number(form.maxCampaigns || 0),
          max_call_minutes_per_month: Number(form.maxCallMinutesPerMonth || 0),
          max_whatsapp_messages_per_month: Number(form.maxWhatsappMessagesPerMonth || 0),
          automated_followups: form.automatedFollowups,
        },
      },
      {
        onSuccess: () => {
          toast({ title: "Plan created", variant: "success" });
          setForm(EMPTY);
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
        <form onSubmit={handleSubmit}>
          <DialogBody className="flex flex-col gap-4">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label htmlFor="plan-code">Code *</Label>
                <Input
                  id="plan-code"
                  required
                  value={form.code}
                  onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))}
                  placeholder="enterprise"
                />
              </div>
              <div>
                <Label htmlFor="plan-name">Name *</Label>
                <Input
                  id="plan-name"
                  required
                  value={form.name}
                  onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
                  placeholder="Enterprise"
                />
              </div>
            </div>
            <div>
              <Label htmlFor="plan-price">Price (₹/month)</Label>
              <Input
                id="plan-price"
                type="number"
                min={0}
                value={form.priceRupees}
                onChange={(e) => setForm((f) => ({ ...f, priceRupees: e.target.value }))}
                placeholder="4900"
              />
            </div>
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
                />
              </div>
              <div>
                <Label htmlFor="plan-campaigns">Max campaigns</Label>
                <Input
                  id="plan-campaigns"
                  type="number"
                  min={0}
                  value={form.maxCampaigns}
                  onChange={(e) => setForm((f) => ({ ...f, maxCampaigns: e.target.value }))}
                />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <Label htmlFor="plan-call-minutes">Call min/mo</Label>
                <Input
                  id="plan-call-minutes"
                  type="number"
                  min={0}
                  value={form.maxCallMinutesPerMonth}
                  onChange={(e) => setForm((f) => ({ ...f, maxCallMinutesPerMonth: e.target.value }))}
                />
              </div>
              <div>
                <Label htmlFor="plan-whatsapp">WhatsApp msgs/mo</Label>
                <Input
                  id="plan-whatsapp"
                  type="number"
                  min={0}
                  value={form.maxWhatsappMessagesPerMonth}
                  onChange={(e) =>
                    setForm((f) => ({ ...f, maxWhatsappMessagesPerMonth: e.target.value }))
                  }
                />
              </div>
            </div>
            <label htmlFor="plan-automated-followups" className="flex items-center gap-2 text-sm text-slate-700 dark:text-slate-300">
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
