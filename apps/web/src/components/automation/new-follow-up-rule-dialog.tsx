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
  Textarea,
  useToast,
} from "@/components/ui";
import { LEAD_STATUS_LABELS, LEAD_STATUS_OPTIONS } from "@/components/leads/status-badge";
import { useCreateFollowUpRule } from "@/lib/hooks";
import type { LeadStatus } from "@/lib/types";

const ruleSchema = z.object({
  name: z.string().trim().min(1, "Rule name is required"),
  delayHours: z.coerce.number().nonnegative("Must be zero or greater"),
  messageTemplate: z.string().trim().min(1, "Message template is required"),
});

type RuleFieldErrors = Partial<Record<"name" | "delayHours" | "messageTemplate", string>>;

export function NewFollowUpRuleDialog() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [status, setStatus] = useState<LeadStatus>("contacted");
  const [delayHours, setDelayHours] = useState("24");
  const [messageTemplate, setMessageTemplate] = useState("");
  const [fieldErrors, setFieldErrors] = useState<RuleFieldErrors>({});
  const createRule = useCreateFollowUpRule();
  const { toast } = useToast();

  function reset() {
    setName("");
    setStatus("contacted");
    setDelayHours("24");
    setMessageTemplate("");
    setFieldErrors({});
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const parsed = ruleSchema.safeParse({ name, delayHours, messageTemplate });
    if (!parsed.success) {
      const errors: RuleFieldErrors = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0] as keyof RuleFieldErrors;
        if (!errors[key]) errors[key] = issue.message;
      }
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});
    createRule.mutate(
      {
        name: parsed.data.name,
        trigger_config: { status, delay_hours: parsed.data.delayHours },
        message_template: parsed.data.messageTemplate,
      },
      {
        onSuccess: () => {
          toast({ title: "Follow-up rule created", variant: "success" });
          reset();
          setOpen(false);
        },
        onError: (err) =>
          toast({ title: "Could not create rule", description: err.message, variant: "error" }),
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger>
        <Button variant="primary" size="md">
          <Plus size={15} aria-hidden />
          New Rule
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>New follow-up rule</DialogTitle>
        <form onSubmit={handleSubmit} noValidate>
          <DialogBody className="flex flex-col gap-4">
            <div>
              <Label htmlFor="rule-name" required>
                Rule name
              </Label>
              <Input
                id="rule-name"
                required
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Nudge contacted leads after a day"
                aria-invalid={fieldErrors.name ? true : undefined}
                aria-describedby={fieldErrors.name ? "rule-name-error" : undefined}
              />
              {fieldErrors.name && (
                <p id="rule-name-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.name}
                </p>
              )}
            </div>
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="rule-status" required>
                  When lead status is
                </Label>
                <Select
                  id="rule-status"
                  value={status}
                  onChange={(v) => setStatus(v as LeadStatus)}
                  className="w-full"
                >
                  {LEAD_STATUS_OPTIONS.map((s) => (
                    <option key={s} value={s}>
                      {LEAD_STATUS_LABELS[s]}
                    </option>
                  ))}
                </Select>
              </div>
              <div>
                <Label htmlFor="rule-delay" required>
                  Wait (hours)
                </Label>
                <input
                  id="rule-delay"
                  type="number"
                  min={0}
                  required
                  value={delayHours}
                  onChange={(e) => setDelayHours(e.target.value)}
                  aria-invalid={fieldErrors.delayHours ? true : undefined}
                  aria-describedby={fieldErrors.delayHours ? "rule-delay-error" : undefined}
                  className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
                />
                {fieldErrors.delayHours && (
                  <p id="rule-delay-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.delayHours}
                  </p>
                )}
              </div>
            </div>
            <div>
              <Label htmlFor="rule-message" required>
                WhatsApp message
              </Label>
              <Textarea
                id="rule-message"
                rows={3}
                required
                value={messageTemplate}
                onChange={(e) => setMessageTemplate(e.target.value)}
                placeholder="Hi! Just checking in — still interested in moving forward?"
                aria-invalid={fieldErrors.messageTemplate ? true : undefined}
                aria-describedby={fieldErrors.messageTemplate ? "rule-message-error" : undefined}
              />
              {fieldErrors.messageTemplate && (
                <p id="rule-message-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.messageTemplate}
                </p>
              )}
              <p className="mt-1.5 text-xs text-slate-400 dark:text-slate-500">
                Only sends automatically for leads captured over WhatsApp — voice leads matching this
                rule still get a task, flagged for manual follow-up.
              </p>
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={createRule.isPending}>
              Create rule
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
