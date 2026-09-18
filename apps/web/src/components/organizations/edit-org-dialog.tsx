"use client";

import { useState } from "react";
import { ChevronDown, Pencil } from "lucide-react";
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
import { OrgFeatureChecklist } from "./org-feature-checklist";
import { useUpdateOrgAdmin, type AdminOrg } from "@/lib/hooks/useAdminOrgs";
import { E164_REGEX, E164_MESSAGE } from "@/lib/phone";

const editOrgSchema = z.object({
  orgName: z.string().trim().min(1, "Organization name is required"),
  adminEmail: z.string().trim().email(),
  adminName: z
    .string()
    .trim()
    .regex(/^[A-Za-z\s'.-]*$/, "Name should only contain letters")
    .optional(),
  adminMobile: z
    .string()
    .trim()
    .optional()
    .refine((v) => !v || E164_REGEX.test(v), E164_MESSAGE),
});

type EditOrgForm = z.infer<typeof editOrgSchema>;
type OrgFieldErrors = Partial<Record<keyof EditOrgForm, string>>;

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

function formFromOrg(org: AdminOrg): EditOrgForm {
  return {
    orgName: org.name,
    adminEmail: org.admin_email ?? "",
    adminName: org.admin_name ?? "",
    adminMobile: org.admin_mobile ?? "",
  };
}

function numbersFromOrg(org: AdminOrg, provider: "plivo" | "twilio"): PhoneNumberEntry[] {
  // Stored digits-only (see apps/api/channels/voice/org_numbers.py) — the
  // list field works in "+"-prefixed E.164 throughout, so numbers coming
  // back from the server need it re-added.
  return org.phone_numbers
    .filter((n) => n.provider === provider)
    .map((n) => ({ phone_number: `+${n.phone_number}`, is_default: n.is_default }));
}

function whatsappNumbersFromOrg(org: AdminOrg): PhoneNumberEntry[] {
  // Meta's phone_number_id, not an E.164 number — no "+" re-added.
  return org.phone_numbers
    .filter((n) => n.provider === "whatsapp")
    .map((n) => ({ phone_number: n.phone_number, is_default: n.is_default }));
}

/**
 * Platform-admin-only: edits an existing org's own profile — name, its
 * admin's email/name/mobile, and its dedicated calling/WhatsApp numbers.
 * The admin's login token itself isn't editable here — that's rotated via
 * RegenerateTokenDialog instead, since a new token can only be shown once.
 * License fields aren't editable here either — see ManageLicenseDialog for
 * issue/renew/extend/suspend/reactivate actions.
 */
export function EditOrgDialog({ org }: { org: AdminOrg }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<EditOrgForm>(() => formFromOrg(org));
  const [fieldErrors, setFieldErrors] = useState<OrgFieldErrors>({});
  const [plivoNumbers, setPlivoNumbers] = useState<PhoneNumberEntry[]>(() => numbersFromOrg(org, "plivo"));
  const [twilioNumbers, setTwilioNumbers] = useState<PhoneNumberEntry[]>(() =>
    numbersFromOrg(org, "twilio"),
  );
  const [whatsappNumbers, setWhatsappNumbers] = useState<PhoneNumberEntry[]>(() =>
    whatsappNumbersFromOrg(org),
  );
  const [credentials, setCredentials] = useState(EMPTY_CREDENTIALS);
  const [credentialsOpen, setCredentialsOpen] = useState(false);
  const [enabledFeatures, setEnabledFeatures] = useState<string[] | null>(
    () => org.enabled_features,
  );
  const [maxTeamMembers, setMaxTeamMembers] = useState(
    () => org.max_team_members?.toString() ?? "",
  );
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
        admin_email: parsed.data.adminEmail.trim(),
        admin_name: parsed.data.adminName?.trim() ?? "",
        admin_mobile: parsed.data.adminMobile?.trim() ?? "",
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
        enabled_features: enabledFeatures,
        max_team_members: maxTeamMembers.trim() ? Number(maxTeamMembers) : null,
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
      setPlivoNumbers(numbersFromOrg(org, "plivo"));
      setTwilioNumbers(numbersFromOrg(org, "twilio"));
      setWhatsappNumbers(whatsappNumbersFromOrg(org));
      setCredentials(EMPTY_CREDENTIALS);
      setCredentialsOpen(false);
      setEnabledFeatures(org.enabled_features);
      setMaxTeamMembers(org.max_team_members?.toString() ?? "");
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
              <Label htmlFor="edit-org-admin-email">Admin email *</Label>
              <Input
                id="edit-org-admin-email"
                type="email"
                required
                value={form.adminEmail}
                onChange={(e) => updateField("adminEmail", e.target.value)}
                aria-invalid={fieldErrors.adminEmail ? true : undefined}
                aria-describedby={fieldErrors.adminEmail ? "edit-org-admin-email-error" : undefined}
              />
              {fieldErrors.adminEmail && (
                <p id="edit-org-admin-email-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.adminEmail}
                </p>
              )}
            </div>
            <div>
              <Label htmlFor="edit-org-admin-name">Admin name</Label>
              <Input
                id="edit-org-admin-name"
                value={form.adminName}
                onChange={(e) => updateField("adminName", e.target.value)}
                placeholder="Optional"
                aria-invalid={fieldErrors.adminName ? true : undefined}
                aria-describedby={fieldErrors.adminName ? "edit-org-admin-name-error" : undefined}
              />
              {fieldErrors.adminName && (
                <p id="edit-org-admin-name-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.adminName}
                </p>
              )}
            </div>
            <div>
              <Label htmlFor="edit-org-admin-mobile">Admin mobile number</Label>
              <Input
                id="edit-org-admin-mobile"
                type="tel"
                inputMode="tel"
                maxLength={16}
                value={form.adminMobile}
                onChange={(e) => updateField("adminMobile", e.target.value.replace(/[^\d+]/g, ""))}
                placeholder="+919876543210"
                aria-invalid={fieldErrors.adminMobile ? true : undefined}
                aria-describedby={fieldErrors.adminMobile ? "edit-org-admin-mobile-error" : undefined}
              />
              {fieldErrors.adminMobile && (
                <p id="edit-org-admin-mobile-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.adminMobile}
                </p>
              )}
            </div>
            <OrgFeatureChecklist
              idPrefix="edit-org"
              enabledFeatures={enabledFeatures}
              onChangeFeatures={setEnabledFeatures}
              maxTeamMembers={maxTeamMembers}
              onChangeMaxTeamMembers={setMaxTeamMembers}
            />
            <PhoneNumberListField
              id="edit-org-plivo-number"
              label="Dedicated Plivo numbers"
              value={plivoNumbers}
              onChange={setPlivoNumbers}
            />
            <PhoneNumberListField
              id="edit-org-twilio-number"
              label="Dedicated Twilio numbers"
              value={twilioNumbers}
              onChange={setTwilioNumbers}
            />
            <PhoneNumberListField
              id="edit-org-whatsapp-number"
              label="Dedicated WhatsApp numbers"
              value={whatsappNumbers}
              onChange={setWhatsappNumbers}
              placeholder="phone_number_id from the Meta dashboard"
              validate={(trimmed) => (trimmed ? undefined : "Enter a phone_number_id")}
              multipleHint="Sends use the Default number unless a campaign pins a specific one."
              defaultLabel="Default"
            />
            <div className="rounded-lg border border-slate-200 dark:border-slate-700">
              <button
                type="button"
                onClick={() => setCredentialsOpen((v) => !v)}
                className="flex w-full items-center justify-between px-3 py-2 text-left text-sm font-medium"
                aria-expanded={credentialsOpen}
              >
                Provider setup
                <ChevronDown
                  size={15}
                  aria-hidden
                  className={`transition-transform ${credentialsOpen ? "rotate-180" : ""}`}
                />
              </button>
              {credentialsOpen && (
                <div className="flex flex-col gap-4 border-t border-slate-200 px-3 py-3 dark:border-slate-700">
                  <p className="text-xs text-slate-500">
                    These fields are blank because stored credentials can&apos;t be read back — fill
                    in only what you want to change. Leave a provider&apos;s fields blank to keep its
                    current credentials.
                  </p>

                  <section className="flex flex-col gap-3 rounded-md border border-slate-200 p-3 dark:border-slate-700">
                    <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                      Plivo
                    </p>
                    <div>
                      <Label htmlFor="edit-plivo-auth-id">Auth ID</Label>
                      <Input
                        id="edit-plivo-auth-id"
                        value={credentials.plivoAuthId}
                        onChange={(e) => setCredentials((c) => ({ ...c, plivoAuthId: e.target.value }))}
                      />
                    </div>
                    <div>
                      <Label htmlFor="edit-plivo-auth-token">Auth token</Label>
                      <Input
                        id="edit-plivo-auth-token"
                        type="password"
                        value={credentials.plivoAuthToken}
                        onChange={(e) =>
                          setCredentials((c) => ({ ...c, plivoAuthToken: e.target.value }))
                        }
                      />
                    </div>
                  </section>

                  <section className="flex flex-col gap-3 rounded-md border border-slate-200 p-3 dark:border-slate-700">
                    <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                      Twilio
                    </p>
                    <div>
                      <Label htmlFor="edit-twilio-account-sid">Account SID</Label>
                      <Input
                        id="edit-twilio-account-sid"
                        value={credentials.twilioAccountSid}
                        onChange={(e) =>
                          setCredentials((c) => ({ ...c, twilioAccountSid: e.target.value }))
                        }
                      />
                    </div>
                    <div>
                      <Label htmlFor="edit-twilio-auth-token">Auth token</Label>
                      <Input
                        id="edit-twilio-auth-token"
                        type="password"
                        value={credentials.twilioAuthToken}
                        onChange={(e) =>
                          setCredentials((c) => ({ ...c, twilioAuthToken: e.target.value }))
                        }
                      />
                    </div>
                  </section>

                  <section className="flex flex-col gap-3 rounded-md border border-slate-200 p-3 dark:border-slate-700">
                    <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                      Meta WhatsApp
                    </p>
                    <div>
                      <Label htmlFor="edit-meta-app-id">App ID</Label>
                      <Input
                        id="edit-meta-app-id"
                        value={credentials.metaAppId}
                        onChange={(e) => setCredentials((c) => ({ ...c, metaAppId: e.target.value }))}
                      />
                    </div>
                    <div>
                      <Label htmlFor="edit-meta-app-secret">App secret</Label>
                      <Input
                        id="edit-meta-app-secret"
                        type="password"
                        value={credentials.metaAppSecret}
                        onChange={(e) =>
                          setCredentials((c) => ({ ...c, metaAppSecret: e.target.value }))
                        }
                      />
                    </div>
                    <div>
                      <Label htmlFor="edit-meta-access-token">Access token</Label>
                      <Input
                        id="edit-meta-access-token"
                        type="password"
                        value={credentials.metaAccessToken}
                        onChange={(e) =>
                          setCredentials((c) => ({ ...c, metaAccessToken: e.target.value }))
                        }
                      />
                    </div>
                    <div>
                      <Label htmlFor="edit-meta-business-account-id">WhatsApp Business Account ID</Label>
                      <Input
                        id="edit-meta-business-account-id"
                        value={credentials.metaBusinessAccountId}
                        onChange={(e) =>
                          setCredentials((c) => ({ ...c, metaBusinessAccountId: e.target.value }))
                        }
                      />
                    </div>
                    <div>
                      <Label htmlFor="edit-meta-verify-token">Webhook verify token</Label>
                      <Input
                        id="edit-meta-verify-token"
                        type="password"
                        value={credentials.metaVerifyToken}
                        onChange={(e) =>
                          setCredentials((c) => ({ ...c, metaVerifyToken: e.target.value }))
                        }
                      />
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
            <Button type="submit" variant="primary" loading={updateOrg.isPending}>
              Save changes
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
