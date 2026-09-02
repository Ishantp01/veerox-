"use client";

import { useState } from "react";
import { Pencil } from "lucide-react";
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
import { useUpdateOrgAdmin, type AdminOrg } from "@/lib/hooks/useAdminOrgs";

const E164_REGEX = /^\+\d{8,15}$/;
const E164_MESSAGE = "Enter a valid E.164 number, e.g. +919876543210";

const editOrgSchema = z.object({
  orgName: z.string().trim().min(1, "Organization name is required"),
  plivoNumber: z
    .string()
    .trim()
    .optional()
    .refine((v) => !v || E164_REGEX.test(v), E164_MESSAGE),
  twilioNumber: z
    .string()
    .trim()
    .optional()
    .refine((v) => !v || E164_REGEX.test(v), E164_MESSAGE),
  whatsappNumberId: z.string().trim().optional(),
});

type EditOrgForm = z.infer<typeof editOrgSchema>;
type OrgFieldErrors = Partial<Record<keyof EditOrgForm, string>>;

function formFromOrg(org: AdminOrg): EditOrgForm {
  return {
    orgName: org.name,
    plivoNumber: org.plivo_phone_number ?? "",
    twilioNumber: org.twilio_phone_number ?? "",
    whatsappNumberId: org.whatsapp_phone_number_id ?? "",
  };
}

/**
 * Platform-admin-only: edits an existing org's own profile — name and its
 * dedicated calling/WhatsApp numbers. Plan/billing status aren't editable
 * here since they're driven by the checkout/payment flow (see
 * apps/api/schemas/billing.py's OrgUpdateIn for why).
 */
export function EditOrgDialog({ org }: { org: AdminOrg }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<EditOrgForm>(() => formFromOrg(org));
  const [fieldErrors, setFieldErrors] = useState<OrgFieldErrors>({});
  const updateOrg = useUpdateOrgAdmin();
  const { toast } = useToast();

  function validateField(key: keyof EditOrgForm, nextForm: EditOrgForm) {
    const result = editOrgSchema.safeParse(nextForm);
    if (result.success) {
      setFieldErrors((prev) => ({ ...prev, [key]: undefined }));
      return;
    }
    const issue = result.error.issues.find((i) => i.path[0] === key);
    setFieldErrors((prev) => ({ ...prev, [key]: issue?.message }));
  }

  function updateField(key: keyof EditOrgForm, value: string) {
    const next = { ...form, [key]: value };
    setForm(next);
    validateField(key, next);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const parsed = editOrgSchema.safeParse(form);
    if (!parsed.success) {
      const errors: OrgFieldErrors = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0] as keyof EditOrgForm;
        if (!errors[key]) errors[key] = issue.message;
      }
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});
    updateOrg.mutate(
      {
        orgId: org.id,
        name: parsed.data.orgName.trim(),
        plivo_phone_number: parsed.data.plivoNumber?.trim() || null,
        twilio_phone_number: parsed.data.twilioNumber?.trim() || null,
        whatsapp_phone_number_id: parsed.data.whatsappNumberId?.trim() || null,
      },
      {
        onSuccess: () => {
          toast({ title: "Organization updated", variant: "success" });
          handleClose(false);
        },
        onError: (err) =>
          toast({ title: "Could not update organization", description: err.message, variant: "error" }),
      }
    );
  }

  function handleClose(next: boolean) {
    setOpen(next);
    if (next) {
      setForm(formFromOrg(org));
    } else {
      setFieldErrors({});
      updateOrg.reset();
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogTrigger>
        <Button variant="ghost" size="sm" aria-label={`Edit ${org.name}`}>
          <Pencil size={14} />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>Edit organization</DialogTitle>
        <form onSubmit={handleSubmit} noValidate>
          <DialogBody className="flex flex-col gap-4">
            <div>
              <Label htmlFor="edit-org-name">Organization name *</Label>
              <Input
                id="edit-org-name"
                required
                value={form.orgName}
                onChange={(e) => updateField("orgName", e.target.value)}
                aria-invalid={fieldErrors.orgName ? true : undefined}
                aria-describedby={fieldErrors.orgName ? "edit-org-name-error" : undefined}
              />
              {fieldErrors.orgName && (
                <p id="edit-org-name-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.orgName}
                </p>
              )}
            </div>
            <div>
              <Label htmlFor="edit-org-plivo-number">Dedicated Plivo number</Label>
              <Input
                id="edit-org-plivo-number"
                type="tel"
                inputMode="tel"
                maxLength={16}
                value={form.plivoNumber}
                onChange={(e) => updateField("plivoNumber", e.target.value.replace(/[^\d+]/g, ""))}
                placeholder="Leave blank to use the default number"
                aria-invalid={fieldErrors.plivoNumber ? true : undefined}
                aria-describedby={fieldErrors.plivoNumber ? "edit-org-plivo-number-error" : undefined}
              />
              {fieldErrors.plivoNumber && (
                <p id="edit-org-plivo-number-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.plivoNumber}
                </p>
              )}
            </div>
            <div>
              <Label htmlFor="edit-org-twilio-number">Dedicated Twilio number</Label>
              <Input
                id="edit-org-twilio-number"
                type="tel"
                inputMode="tel"
                maxLength={16}
                value={form.twilioNumber}
                onChange={(e) => updateField("twilioNumber", e.target.value.replace(/[^\d+]/g, ""))}
                placeholder="Leave blank to use the default number"
                aria-invalid={fieldErrors.twilioNumber ? true : undefined}
                aria-describedby={fieldErrors.twilioNumber ? "edit-org-twilio-number-error" : undefined}
              />
              {fieldErrors.twilioNumber && (
                <p id="edit-org-twilio-number-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.twilioNumber}
                </p>
              )}
            </div>
            <div>
              <Label htmlFor="edit-org-whatsapp-number">Dedicated WhatsApp number ID</Label>
              <Input
                id="edit-org-whatsapp-number"
                value={form.whatsappNumberId}
                onChange={(e) => updateField("whatsappNumberId", e.target.value)}
                placeholder="Leave blank to use the default number"
                aria-invalid={fieldErrors.whatsappNumberId ? true : undefined}
                aria-describedby={
                  fieldErrors.whatsappNumberId ? "edit-org-whatsapp-number-error" : undefined
                }
              />
              {fieldErrors.whatsappNumberId && (
                <p id="edit-org-whatsapp-number-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.whatsappNumberId}
                </p>
              )}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => handleClose(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={updateOrg.isPending}>
              Save changes
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
