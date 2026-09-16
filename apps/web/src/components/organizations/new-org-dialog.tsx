"use client";

import { useState } from "react";
import { ChevronDown, Plus } from "lucide-react";
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
import { PhoneNumberListField, type PhoneNumberEntry } from "./phone-number-list-field";
import { useProvisionOrg, type ProvisionOrgResult } from "@/lib/hooks/useAdminOrgs";
import { E164_REGEX, E164_MESSAGE } from "@/lib/phone";

const EMPTY = {
  orgName: "",
  email: "",
  fullName: "",
  mobile: "",
};

const EMPTY_CREDENTIALS = {
  plivoAuthId: "",
  plivoAuthToken: "",
  twilioAccountSid: "",
  twilioAuthToken: "",
  metaAppId: "",
  metaAppSecret: "",
  metaAccessToken: "",
  metaBusinessAccountId: "",
  metaVerifyToken: "",
};

const orgSchema = z.object({
  orgName: z.string().trim().min(1, "Organization name is required"),
  email: z.string().trim().email(),
  fullName: z
    .string()
    .trim()
    .regex(/^[A-Za-z\s'.-]*$/, "Name should only contain letters")
    .optional(),
  mobile: z.string().trim().regex(E164_REGEX, E164_MESSAGE),
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
  const [plivoNumbers, setPlivoNumbers] = useState<PhoneNumberEntry[]>([]);
  const [twilioNumbers, setTwilioNumbers] = useState<PhoneNumberEntry[]>([]);
  const [whatsappNumbers, setWhatsappNumbers] = useState<PhoneNumberEntry[]>([]);
  const [credentials, setCredentials] = useState(EMPTY_CREDENTIALS);
  const [credentialsOpen, setCredentialsOpen] = useState(false);
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
        phone_numbers: [
          ...plivoNumbers.map((n) => ({ provider: "plivo" as const, ...n })),
          ...twilioNumbers.map((n) => ({ provider: "twilio" as const, ...n })),
          ...whatsappNumbers.map((n) => ({ provider: "whatsapp" as const, ...n })),
        ],
        plivo_auth_id: credentials.plivoAuthId.trim() || undefined,
        plivo_auth_token: credentials.plivoAuthToken.trim() || undefined,
        twilio_account_sid: credentials.twilioAccountSid.trim() || undefined,
        twilio_auth_token: credentials.twilioAuthToken.trim() || undefined,
        meta_app_id: credentials.metaAppId.trim() || undefined,
        meta_app_secret: credentials.metaAppSecret.trim() || undefined,
        meta_access_token: credentials.metaAccessToken.trim() || undefined,
        meta_whatsapp_business_account_id: credentials.metaBusinessAccountId.trim() || undefined,
        meta_verify_token: credentials.metaVerifyToken.trim() || undefined,
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
      setPlivoNumbers([]);
      setTwilioNumbers([]);
      setWhatsappNumbers([]);
      setCredentials(EMPTY_CREDENTIALS);
      setCredentialsOpen(false);
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
              <div className="rounded-lg border border-slate-200 dark:border-slate-700">
                <button
                  type="button"
                  onClick={() => setCredentialsOpen((v) => !v)}
                  className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium"
                  aria-expanded={credentialsOpen}
                >
                  Provider setup (optional)
                  <ChevronDown
                    size={15}
                    aria-hidden
                    className={`transition-transform ${credentialsOpen ? "rotate-180" : ""}`}
                  />
                </button>
                {credentialsOpen && (
                  <div className="flex flex-col gap-4 border-t border-slate-200 px-3 py-3 dark:border-slate-700">
                    <p className="text-xs text-slate-500">
                      Each org calls/messages through its own Plivo, Twilio, and Meta WhatsApp
                      accounts — there is no shared platform fallback. Leave any of these blank to
                      configure them later from the org&apos;s own settings page.
                    </p>

                    {/* Plivo: dedicated numbers + account credentials together. */}
                    <section className="flex flex-col gap-3 rounded-md border border-slate-200 p-3 dark:border-slate-700">
                      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                        Plivo
                      </p>
                      <PhoneNumberListField
                        id="org-plivo-number"
                        label="Dedicated numbers"
                        value={plivoNumbers}
                        onChange={setPlivoNumbers}
                      />
                      <div>
                        <Label htmlFor="plivo-auth-id">Auth ID</Label>
                        <Input
                          id="plivo-auth-id"
                          value={credentials.plivoAuthId}
                          onChange={(e) =>
                            setCredentials((c) => ({ ...c, plivoAuthId: e.target.value }))
                          }
                        />
                      </div>
                      <div>
                        <Label htmlFor="plivo-auth-token">Auth token</Label>
                        <Input
                          id="plivo-auth-token"
                          type="password"
                          value={credentials.plivoAuthToken}
                          onChange={(e) =>
                            setCredentials((c) => ({ ...c, plivoAuthToken: e.target.value }))
                          }
                        />
                      </div>
                    </section>

                    {/* Twilio: dedicated numbers + account credentials together. */}
                    <section className="flex flex-col gap-3 rounded-md border border-slate-200 p-3 dark:border-slate-700">
                      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                        Twilio
                      </p>
                      <PhoneNumberListField
                        id="org-twilio-number"
                        label="Dedicated numbers"
                        value={twilioNumbers}
                        onChange={setTwilioNumbers}
                      />
                      <p className="text-xs text-slate-500">
                        Add numbers on both Plivo and Twilio to give this org dedicated lines on
                        each provider — the calling page then lets them choose which one to dial
                        from.
                      </p>
                      <div>
                        <Label htmlFor="twilio-account-sid">Account SID</Label>
                        <Input
                          id="twilio-account-sid"
                          value={credentials.twilioAccountSid}
                          onChange={(e) =>
                            setCredentials((c) => ({ ...c, twilioAccountSid: e.target.value }))
                          }
                        />
                      </div>
                      <div>
                        <Label htmlFor="twilio-auth-token">Auth token</Label>
                        <Input
                          id="twilio-auth-token"
                          type="password"
                          value={credentials.twilioAuthToken}
                          onChange={(e) =>
                            setCredentials((c) => ({ ...c, twilioAuthToken: e.target.value }))
                          }
                        />
                      </div>
                    </section>

                    {/* Meta WhatsApp: dedicated number(s) + App credentials + webhook
                        verify token together — saving these auto-registers the
                        webhook against this org's own Meta App (see
                        channels/whatsapp/webhook_registration.py), no manual
                        dashboard step needed. */}
                    <section className="flex flex-col gap-3 rounded-md border border-slate-200 p-3 dark:border-slate-700">
                      <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                        Meta WhatsApp
                      </p>
                      <PhoneNumberListField
                        id="org-whatsapp-number"
                        label="Dedicated numbers"
                        value={whatsappNumbers}
                        onChange={setWhatsappNumbers}
                        placeholder="phone_number_id from the Meta dashboard"
                        validate={(trimmed) => (trimmed ? undefined : "Enter a phone_number_id")}
                        multipleHint="Sends use the Default number unless a campaign pins a specific one."
                        defaultLabel="Default"
                      />
                      <div>
                        <Label htmlFor="meta-app-id">App ID</Label>
                        <Input
                          id="meta-app-id"
                          value={credentials.metaAppId}
                          onChange={(e) => setCredentials((c) => ({ ...c, metaAppId: e.target.value }))}
                        />
                      </div>
                      <div>
                        <Label htmlFor="meta-app-secret">App secret</Label>
                        <Input
                          id="meta-app-secret"
                          type="password"
                          value={credentials.metaAppSecret}
                          onChange={(e) =>
                            setCredentials((c) => ({ ...c, metaAppSecret: e.target.value }))
                          }
                        />
                      </div>
                      <div>
                        <Label htmlFor="meta-access-token">Access token</Label>
                        <Input
                          id="meta-access-token"
                          type="password"
                          value={credentials.metaAccessToken}
                          onChange={(e) =>
                            setCredentials((c) => ({ ...c, metaAccessToken: e.target.value }))
                          }
                        />
                      </div>
                      <div>
                        <Label htmlFor="meta-business-account-id">WhatsApp Business Account ID</Label>
                        <Input
                          id="meta-business-account-id"
                          value={credentials.metaBusinessAccountId}
                          onChange={(e) =>
                            setCredentials((c) => ({ ...c, metaBusinessAccountId: e.target.value }))
                          }
                        />
                      </div>
                      <div>
                        <Label htmlFor="meta-verify-token">Webhook verify token</Label>
                        <Input
                          id="meta-verify-token"
                          type="password"
                          value={credentials.metaVerifyToken}
                          onChange={(e) =>
                            setCredentials((c) => ({ ...c, metaVerifyToken: e.target.value }))
                          }
                        />
                        <p className="mt-1.5 text-xs text-slate-500">
                          Pick any string — this org&apos;s Meta App webhook gets registered against it
                          automatically once saved.
                        </p>
                      </div>
                    </section>
                  </div>
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
