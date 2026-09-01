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
import { useProvisionOrg, type ProvisionOrgResult } from "@/lib/hooks/useAdminOrgs";

const EMPTY = {
  orgName: "",
  email: "",
  fullName: "",
  mobile: "",
  plivoNumber: "",
  twilioNumber: "",
  whatsappNumberId: "",
};

const E164_REGEX = /^\+\d{8,15}$/;
const E164_MESSAGE = "Enter a valid E.164 number, e.g. +919876543210";

const orgSchema = z.object({
  orgName: z.string().trim().min(1, "Organization name is required"),
  email: z.string().trim().email(),
  fullName: z
    .string()
    .trim()
    .regex(/^[A-Za-z\s'.-]*$/, "Name should only contain letters")
    .optional(),
  mobile: z.string().trim().regex(E164_REGEX, E164_MESSAGE),
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
  whatsappNumberId: z
    .string()
    .trim()
    .optional()
    .refine((v) => !v || E164_REGEX.test(v), E164_MESSAGE),
});

type OrgFieldErrors = Partial<Record<keyof typeof EMPTY, string>>;

/**
 * Platform-admin-only: creates a brand new org + admin account. There's no
 * self-registration (see apps/api/routers/auth.py's provision_org) — this
 * dialog is the only way a new customer org comes into existence. The
 * generated login token is the admin's sole credential and is shown exactly
 * once here; no email provider is wired up, so it must be copied and
 * handed to them directly.
 */
export function NewOrgDialog() {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [fieldErrors, setFieldErrors] = useState<OrgFieldErrors>({});
  const [result, setResult] = useState<ProvisionOrgResult | null>(null);
  const provisionOrg = useProvisionOrg();
  const { toast } = useToast();

  function validateField(key: keyof typeof EMPTY, nextForm: typeof EMPTY) {
    const result = orgSchema.safeParse(nextForm);
    if (result.success) {
      setFieldErrors((prev) => ({ ...prev, [key]: undefined }));
      return;
    }
    const issue = result.error.issues.find((i) => i.path[0] === key);
    setFieldErrors((prev) => ({ ...prev, [key]: issue?.message }));
  }

  function updateField(key: keyof typeof EMPTY, value: string) {
    const next = { ...form, [key]: value };
    setForm(next);
    validateField(key, next);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const parsed = orgSchema.safeParse(form);
    if (!parsed.success) {
      const errors: OrgFieldErrors = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0] as keyof typeof EMPTY;
        if (!errors[key]) errors[key] = issue.message;
      }
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});
    provisionOrg.mutate(
      {
        org_name: form.orgName.trim(),
        email: form.email.trim(),
        full_name: form.fullName.trim() || undefined,
        mobile: form.mobile.trim(),
        plivo_phone_number: form.plivoNumber.trim() || undefined,
        twilio_phone_number: form.twilioNumber.trim() || undefined,
        whatsapp_phone_number_id: form.whatsappNumberId.trim() || undefined,
      },
      {
        onSuccess: (res) => {
          setResult(res);
          toast({ title: "Organization created", variant: "success" });
        },
        onError: (err) =>
          toast({ title: "Could not create organization", description: err.message, variant: "error" }),
      }
    );
  }

  function handleClose(next: boolean) {
    setOpen(next);
    if (!next) {
      setForm(EMPTY);
      setFieldErrors({});
      setResult(null);
      provisionOrg.reset();
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogTrigger>
        <Button variant="primary" size="md">
          <Plus size={15} aria-hidden />
          New organization
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>New organization</DialogTitle>
        {result ? (
          <>
            <DialogBody className="flex flex-col gap-3">
              <p>
                Give both of these to <strong>{result.email}</strong> — the token is their only way to
                sign in, and it won&apos;t be shown again.
              </p>
              {/* SMS status line removed from the UI — see removefeature.md
                  ("SMS notification text"). The backend still attempts the
                  SMS send and still returns sms_sent; this screen just no
                  longer reports on it. */}
              <div>
                <Label>Email</Label>
                <code className="block break-all rounded-lg bg-slate-100 px-3 py-2 text-xs dark:bg-slate-800">
                  {result.email}
                </code>
              </div>
              <div>
                <Label>Login token</Label>
                <code className="block break-all rounded-lg bg-slate-100 px-3 py-2 text-xs dark:bg-slate-800">
                  {result.login_token}
                </code>
              </div>
            </DialogBody>
            <DialogFooter>
              <Button variant="primary" onClick={() => handleClose(false)}>
                Done
              </Button>
            </DialogFooter>
          </>
        ) : (
          <form onSubmit={handleSubmit} noValidate>
            <DialogBody className="flex flex-col gap-4">
              <div>
                <Label htmlFor="org-name">Organization name *</Label>
                <Input
                  id="org-name"
                  required
                  value={form.orgName}
                  onChange={(e) => updateField("orgName", e.target.value)}
                  placeholder="Acme Inc."
                  aria-invalid={fieldErrors.orgName ? true : undefined}
                  aria-describedby={fieldErrors.orgName ? "org-name-error" : undefined}
                />
                {fieldErrors.orgName && (
                  <p id="org-name-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.orgName}
                  </p>
                )}
              </div>
              <div>
                <Label htmlFor="admin-email">Admin email *</Label>
                <Input
                  id="admin-email"
                  type="email"
                  required
                  value={form.email}
                  onChange={(e) => updateField("email", e.target.value)}
                  placeholder="admin@acme.com"
                  aria-invalid={fieldErrors.email ? true : undefined}
                  aria-describedby={fieldErrors.email ? "admin-email-error" : undefined}
                />
                {fieldErrors.email && (
                  <p id="admin-email-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.email}
                  </p>
                )}
              </div>
              <div>
                <Label htmlFor="admin-name">Admin name</Label>
                <Input
                  id="admin-name"
                  value={form.fullName}
                  onChange={(e) => updateField("fullName", e.target.value)}
                  placeholder="Optional"
                  aria-invalid={fieldErrors.fullName ? true : undefined}
                  aria-describedby={fieldErrors.fullName ? "admin-name-error" : undefined}
                />
                {fieldErrors.fullName && (
                  <p id="admin-name-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.fullName}
                  </p>
                )}
              </div>
              <div>
                <Label htmlFor="admin-mobile">Admin mobile number *</Label>
                <Input
                  id="admin-mobile"
                  type="tel"
                  inputMode="tel"
                  required
                  maxLength={16}
                  value={form.mobile}
                  onChange={(e) => updateField("mobile", e.target.value.replace(/[^\d+]/g, ""))}
                  placeholder="+919876543210"
                  aria-invalid={fieldErrors.mobile ? true : undefined}
                  aria-describedby={fieldErrors.mobile ? "admin-mobile-error" : undefined}
                />
                {fieldErrors.mobile && (
                  <p id="admin-mobile-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.mobile}
                  </p>
                )}
              </div>
              <div>
                <Label htmlFor="org-plivo-number">Dedicated Plivo number</Label>
                <Input
                  id="org-plivo-number"
                  type="tel"
                  inputMode="tel"
                  maxLength={16}
                  value={form.plivoNumber}
                  onChange={(e) => updateField("plivoNumber", e.target.value.replace(/[^\d+]/g, ""))}
                  placeholder="Optional — leave blank to use the default number"
                  aria-invalid={fieldErrors.plivoNumber ? true : undefined}
                  aria-describedby={fieldErrors.plivoNumber ? "org-plivo-number-error" : undefined}
                />
                {fieldErrors.plivoNumber && (
                  <p id="org-plivo-number-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.plivoNumber}
                  </p>
                )}
              </div>
              <div>
                <Label htmlFor="org-twilio-number">Dedicated Twilio number</Label>
                <Input
                  id="org-twilio-number"
                  type="tel"
                  inputMode="tel"
                  maxLength={16}
                  value={form.twilioNumber}
                  onChange={(e) => updateField("twilioNumber", e.target.value.replace(/[^\d+]/g, ""))}
                  placeholder="Optional — leave blank to use the default number"
                  aria-invalid={fieldErrors.twilioNumber ? true : undefined}
                  aria-describedby={fieldErrors.twilioNumber ? "org-twilio-number-error" : undefined}
                />
                <p className="mt-1 text-xs text-slate-500">
                  Set both to give this org a dedicated number on each provider — the calling
                  page then lets them choose which one to dial from.
                </p>
                {fieldErrors.twilioNumber && (
                  <p id="org-twilio-number-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.twilioNumber}
                  </p>
                )}
              </div>
              <div>
                <Label htmlFor="org-whatsapp-number">Dedicated WhatsApp number ID</Label>
                <Input
                  id="org-whatsapp-number"
                  value={form.whatsappNumberId}
                  onChange={(e) => updateField("whatsappNumberId", e.target.value)}
                  placeholder="Optional — leave blank to use the default number"
                  aria-invalid={fieldErrors.whatsappNumberId ? true : undefined}
                  aria-describedby={fieldErrors.whatsappNumberId ? "org-whatsapp-number-error" : undefined}
                />
                {fieldErrors.whatsappNumberId && (
                  <p id="org-whatsapp-number-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.whatsappNumberId}
                  </p>
                )}
              </div>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => handleClose(false)}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" loading={provisionOrg.isPending}>
                Create organization
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
