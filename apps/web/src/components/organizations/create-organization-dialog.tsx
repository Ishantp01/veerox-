"use client";

import { useState } from "react";
import { ChevronDown, ChevronLeft, Plus, Server, Users } from "lucide-react";
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
import { SetupInstructionsView } from "./setup-instructions-view";
import { useIssueLicense, useProvisionOrg, type ProvisionOrgResult } from "@/lib/hooks/useAdminOrgs";
import { useCreateClientOrg, type CreateClientOrgResult } from "@/lib/hooks/usePlatformOrgs";
import { CountryCodeField } from "@/components/common/country-code-field";
import { DEFAULT_COUNTRY_CODE, E164_REGEX, E164_MESSAGE } from "@/lib/phone";

type HostingType = "shared" | "separate";

const EMPTY = { orgName: "", email: "", fullName: "", mobile: "" };

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
  openaiApiKey: "",
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
 * One "Create organization" button, one dialog — the first screen asks
 * where this organization will run, since the two answers need genuinely
 * different information and produce a genuinely different one-time secret
 * (a login token vs. a deployment setup token). Having two separate
 * buttons/dialogs for this was confusing, since both are colloquially
 * "creating an organization".
 */
export function CreateOrganizationDialog() {
  const [open, setOpen] = useState(false);
  const [hostingType, setHostingType] = useState<HostingType | null>(null);

  function handleClose(next: boolean) {
    setOpen(next);
    if (!next) {
      // Reset happens after the close animation would read oddly mid-dialog,
      // but there's no exit animation here, so resetting immediately is fine.
      setHostingType(null);
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogTrigger>
        <Button variant="primary" size="md">
          <Plus size={15} aria-hidden />
          Create organization
        </Button>
      </DialogTrigger>
      <DialogContent>
        {hostingType === null && (
          <>
            <DialogTitle>Create organization</DialogTitle>
            <DialogBody className="flex flex-col gap-3">
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Where will this organization run?
              </p>
              <button
                type="button"
                onClick={() => setHostingType("shared")}
                className="flex items-start gap-3 rounded-lg border border-slate-200 p-4 text-left transition-colors hover:border-primary-400 hover:bg-primary-50/50 dark:border-slate-700 dark:hover:border-primary-500 dark:hover:bg-primary-500/10"
              >
                <Users size={20} className="mt-0.5 shrink-0 text-primary-600 dark:text-primary-400" aria-hidden />
                <span>
                  <span className="block font-semibold text-slate-900 dark:text-slate-100">
                    On this shared platform
                  </span>
                  <span className="block text-sm text-slate-500 dark:text-slate-400">
                    Their team signs in to this same dashboard. You get an admin login token to hand
                    them.
                  </span>
                </span>
              </button>
              <button
                type="button"
                onClick={() => setHostingType("separate")}
                className="flex items-start gap-3 rounded-lg border border-slate-200 p-4 text-left transition-colors hover:border-primary-400 hover:bg-primary-50/50 dark:border-slate-700 dark:hover:border-primary-500 dark:hover:bg-primary-500/10"
              >
                <Server size={20} className="mt-0.5 shrink-0 text-primary-600 dark:text-primary-400" aria-hidden />
                <span>
                  <span className="block font-semibold text-slate-900 dark:text-slate-100">
                    On their own separate server
                  </span>
                  <span className="block text-sm text-slate-500 dark:text-slate-400">
                    Their own database and hosting. You get a setup token + configuration to hand to
                    whoever sets up their server.
                  </span>
                </span>
              </button>
            </DialogBody>
            <DialogFooter>
              <Button variant="outline" onClick={() => handleClose(false)}>
                Cancel
              </Button>
            </DialogFooter>
          </>
        )}
        {hostingType === "shared" && (
          <SharedOrgForm onBack={() => setHostingType(null)} onDone={() => handleClose(false)} />
        )}
        {hostingType === "separate" && (
          <SeparateOrgForm onBack={() => setHostingType(null)} onDone={() => handleClose(false)} />
        )}
      </DialogContent>
    </Dialog>
  );
}

function BackButton({ onBack, disabled }: { onBack: () => void; disabled?: boolean }) {
  return (
    <button
      type="button"
      onClick={onBack}
      disabled={disabled}
      title={disabled ? "Wait for this to finish before going back" : undefined}
      className="mb-1 flex items-center gap-1 text-xs font-medium text-slate-500 hover:text-slate-700 disabled:cursor-not-allowed disabled:opacity-40 dark:text-slate-400 dark:hover:text-slate-200"
    >
      <ChevronLeft size={13} aria-hidden />
      Back
    </button>
  );
}

/** "On this shared platform" — creates the org + an admin login (POST /auth/provision-org). */
function SharedOrgForm({ onBack, onDone }: { onBack: () => void; onDone: () => void }) {
  const [form, setForm] = useState(EMPTY);
  const [countryCode, setCountryCode] = useState(DEFAULT_COUNTRY_CODE);
  const [fieldErrors, setFieldErrors] = useState<OrgFieldErrors>({});
  const [plivoNumbers, setPlivoNumbers] = useState<PhoneNumberEntry[]>([]);
  const [twilioNumbers, setTwilioNumbers] = useState<PhoneNumberEntry[]>([]);
  const [whatsappNumbers, setWhatsappNumbers] = useState<PhoneNumberEntry[]>([]);
  const [credentials, setCredentials] = useState(EMPTY_CREDENTIALS);
  const [credentialsOpen, setCredentialsOpen] = useState(false);
  const [licenseDays, setLicenseDays] = useState("30");
  const [enabledFeatures, setEnabledFeatures] = useState<string[] | null>(null);
  const [maxTeamMembers, setMaxTeamMembers] = useState("");
  const [result, setResult] = useState<ProvisionOrgResult | null>(null);
  const provisionOrg = useProvisionOrg();
  const issueLicense = useIssueLicense();
  const { toast } = useToast();

  function validateField(key: keyof typeof EMPTY, nextForm: typeof EMPTY) {
    const parsed = orgSchema.safeParse(nextForm);
    if (parsed.success) {
      setFieldErrors((prev) => ({ ...prev, [key]: undefined }));
      return;
    }
    const issue = parsed.error.issues.find((i) => i.path[0] === key);
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
        default_country_code: countryCode,
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
        openai_api_key: credentials.openaiApiKey.trim() || undefined,
        enabled_features: enabledFeatures,
        max_team_members: maxTeamMembers.trim() ? Number(maxTeamMembers) : undefined,
      },
      {
        onSuccess: (res) => {
          setResult(res);
          toast({ title: "Organization created", variant: "success" });
          const parsedDays = Number(licenseDays);
          if (Number.isFinite(parsedDays) && parsedDays > 0) {
            issueLicense.mutate(
              { orgId: res.org_id, days: parsedDays },
              {
                onError: (err) =>
                  toast({
                    title: "Organization created, but the license couldn't be issued",
                    description: `${err.message} — issue it from the Organizations page instead.`,
                    variant: "error",
                  }),
              }
            );
          }
        },
        onError: (err) =>
          toast({ title: "Could not create organization", description: err.message, variant: "error" }),
      }
    );
  }

  if (result) {
    return (
      <>
        <DialogTitle>Organization created</DialogTitle>
        <DialogBody className="flex flex-col gap-3">
          <p>
            Give both of these to <strong>{result.email}</strong> — the token is their only way to
            sign in, and it won&apos;t be shown again.
          </p>
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
          {issueLicense.isSuccess && (
            <p className="text-xs text-emerald-600 dark:text-emerald-400">
              License issued — active for {licenseDays} days.
            </p>
          )}
          {issueLicense.isPending && (
            <p className="text-xs text-slate-500 dark:text-slate-400">Issuing license…</p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="primary" onClick={onDone}>
            Done
          </Button>
        </DialogFooter>
      </>
    );
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <BackButton onBack={onBack} disabled={provisionOrg.isPending || issueLicense.isPending} />
      <DialogTitle>New organization — shared platform</DialogTitle>
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
          <CountryCodeField id="org-country-code" value={countryCode} onChange={setCountryCode} />
          <p className="mt-1 text-xs text-slate-400">
            Added automatically to any phone number this organization enters without one (calling,
            WhatsApp, contacts, campaigns).
          </p>
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
          <Label htmlFor="license-days">License duration (days)</Label>
          <Input
            id="license-days"
            type="number"
            min={1}
            value={licenseDays}
            onChange={(e) => setLicenseDays(e.target.value)}
            placeholder="30"
          />
          <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
            Issues an active license good for this many days from now. Leave blank to skip — the org
            can still be used, and a license issued later from the Organizations page.
          </p>
        </div>
        <OrgFeatureChecklist
          idPrefix="new-org"
          enabledFeatures={enabledFeatures}
          onChangeFeatures={setEnabledFeatures}
          maxTeamMembers={maxTeamMembers}
          onChangeMaxTeamMembers={setMaxTeamMembers}
        />
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
                Each org calls/messages through its own Plivo, Twilio, and Meta WhatsApp accounts —
                there is no shared platform fallback. Leave any of these blank to configure them
                later from the org&apos;s own settings page.
              </p>

              <section className="flex flex-col gap-3 rounded-md border border-slate-200 p-3 dark:border-slate-700">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Plivo</p>
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
                    onChange={(e) => setCredentials((c) => ({ ...c, plivoAuthId: e.target.value }))}
                  />
                </div>
                <div>
                  <Label htmlFor="plivo-auth-token">Auth token</Label>
                  <Input
                    id="plivo-auth-token"
                    type="password"
                    value={credentials.plivoAuthToken}
                    onChange={(e) => setCredentials((c) => ({ ...c, plivoAuthToken: e.target.value }))}
                  />
                </div>
              </section>

              <section className="flex flex-col gap-3 rounded-md border border-slate-200 p-3 dark:border-slate-700">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Twilio</p>
                <PhoneNumberListField
                  id="org-twilio-number"
                  label="Dedicated numbers"
                  value={twilioNumbers}
                  onChange={setTwilioNumbers}
                />
                <p className="text-xs text-slate-500">
                  Add numbers on both Plivo and Twilio to give this org dedicated lines on each
                  provider — the calling page then lets them choose which one to dial from.
                </p>
                <div>
                  <Label htmlFor="twilio-account-sid">Account SID</Label>
                  <Input
                    id="twilio-account-sid"
                    value={credentials.twilioAccountSid}
                    onChange={(e) => setCredentials((c) => ({ ...c, twilioAccountSid: e.target.value }))}
                  />
                </div>
                <div>
                  <Label htmlFor="twilio-auth-token">Auth token</Label>
                  <Input
                    id="twilio-auth-token"
                    type="password"
                    value={credentials.twilioAuthToken}
                    onChange={(e) => setCredentials((c) => ({ ...c, twilioAuthToken: e.target.value }))}
                  />
                </div>
              </section>

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
                    onChange={(e) => setCredentials((c) => ({ ...c, metaAppSecret: e.target.value }))}
                  />
                </div>
                <div>
                  <Label htmlFor="meta-access-token">Access token</Label>
                  <Input
                    id="meta-access-token"
                    type="password"
                    value={credentials.metaAccessToken}
                    onChange={(e) => setCredentials((c) => ({ ...c, metaAccessToken: e.target.value }))}
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
                    onChange={(e) => setCredentials((c) => ({ ...c, metaVerifyToken: e.target.value }))}
                  />
                  <p className="mt-1.5 text-xs text-slate-500">
                    Pick any string — this org&apos;s Meta App webhook gets registered against it
                    automatically once saved.
                  </p>
                </div>
              </section>

              <section className="flex flex-col gap-3 rounded-md border border-slate-200 p-3 dark:border-slate-700">
                <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">OpenAI</p>
                <div>
                  <Label htmlFor="openai-api-key">API key</Label>
                  <Input
                    id="openai-api-key"
                    type="password"
                    value={credentials.openaiApiKey}
                    onChange={(e) => setCredentials((c) => ({ ...c, openaiApiKey: e.target.value }))}
                    placeholder="sk-..."
                  />
                  <p className="mt-1.5 text-xs text-slate-500">
                    Leave blank to use the platform&apos;s shared key — can be set or changed later
                    from the org&apos;s own settings page.
                  </p>
                </div>
              </section>
            </div>
          )}
        </div>
      </DialogBody>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={provisionOrg.isPending}>
          Create organization
        </Button>
      </DialogFooter>
    </form>
  );
}

/** "On their own separate server" — creates the org + its default
 * deployment/setup token (POST /platform/organizations). */
function SeparateOrgForm({ onBack, onDone }: { onBack: () => void; onDone: () => void }) {
  const [name, setName] = useState("");
  const [licenseDays, setLicenseDays] = useState("365");
  const [enabledFeatures, setEnabledFeatures] = useState<string[] | null>(null);
  const [maxTeamMembers, setMaxTeamMembers] = useState("");
  const [idempotencyKey] = useState(() => crypto.randomUUID());
  const [result, setResult] = useState<CreateClientOrgResult | null>(null);
  const createOrg = useCreateClientOrg();
  const { toast } = useToast();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    const days = licenseDays.trim() ? Number(licenseDays) : undefined;
    createOrg.mutate(
      {
        name: trimmed,
        enabled_features: enabledFeatures,
        license_days: days && Number.isFinite(days) && days > 0 ? days : undefined,
        max_team_members: maxTeamMembers.trim() ? Number(maxTeamMembers) : undefined,
        idempotency_key: idempotencyKey,
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

  if (result) {
    return (
      <>
        <DialogTitle>Organization created</DialogTitle>
        <DialogBody className="flex flex-col gap-3">
          <p>
            <strong>{result.organization.name}</strong> is created. This does not set up any hosting
            by itself — send the configuration below to whoever sets up this client&apos;s own
            server.
          </p>
          {result.setup ? (
            <SetupInstructionsView setup={result.setup} />
          ) : (
            <p className="text-xs text-slate-500 dark:text-slate-400">
              This organization already existed — no new setup token was generated.
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <Button variant="primary" onClick={onDone}>
            Done
          </Button>
        </DialogFooter>
      </>
    );
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
      <BackButton onBack={onBack} disabled={createOrg.isPending} />
      <DialogTitle>New organization — own server</DialogTitle>
      <DialogBody className="flex flex-col gap-4">
        <div>
          <Label htmlFor="client-org-name">Organization name *</Label>
          <Input
            id="client-org-name"
            required
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Acme Corp"
          />
        </div>
        <div>
          <Label htmlFor="client-org-license-days">License duration (days)</Label>
          <Input
            id="client-org-license-days"
            type="number"
            min={1}
            value={licenseDays}
            onChange={(e) => setLicenseDays(e.target.value)}
            placeholder="365"
          />
          <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
            Issues an active license good for this many days from now. Leave blank to create it
            without a license yet.
          </p>
        </div>
        <OrgFeatureChecklist
          idPrefix="client-org"
          enabledFeatures={enabledFeatures}
          onChangeFeatures={setEnabledFeatures}
          maxTeamMembers={maxTeamMembers}
          onChangeMaxTeamMembers={setMaxTeamMembers}
        />
      </DialogBody>
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onDone}>
          Cancel
        </Button>
        <Button type="submit" variant="primary" loading={createOrg.isPending} disabled={!name.trim()}>
          Create organization
        </Button>
      </DialogFooter>
    </form>
  );
}
