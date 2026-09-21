"use client";

import { useState, type ReactNode } from "react";
import { Bot, ChevronRight, Globe, KeyRound, Phone, Users } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  Input,
  Label,
  Select,
  Skeleton,
  useToast,
} from "@/components/ui";
import {
  useCallingSettings,
  useCountryCodeSettings,
  useUpdateCountryCodeSettings,
  useDeleteMetaCredentialsSettings,
  useDeletePlivoCredentialsSettings,
  useDeleteTwilioCredentialsSettings,
  useMetaCredentialsSettings,
  usePlivoCredentialsSettings,
  useTemplates,
  useTwilioCredentialsSettings,
  useUpdateCallingSettings,
  useUpdateMetaCredentialsSettings,
  useUpdatePlivoCredentialsSettings,
  useUpdateTwilioCredentialsSettings,
  useUpdateWhatsAppSettings,
  useWhatsAppSettings,
} from "@/lib/hooks";
import { useAuth } from "@/lib/auth-context";
import { CountryCodeField } from "@/components/common/country-code-field";
import { ScriptLibrary } from "./script-library";

interface CollapsibleSectionProps {
  title: string;
  icon: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
  /** Card's max-width utility class — wider sections (e.g. the calling
   * script library's table) override the max-w-3xl default. */
  maxWidthClassName?: string;
}

function CollapsibleSection({
  title,
  icon,
  defaultOpen = false,
  children,
  maxWidthClassName = "max-w-3xl",
}: CollapsibleSectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  const contentId = `section-${title.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <Card className={maxWidthClassName}>
      <CardHeader className="p-0">
        <button
          type="button"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          aria-controls={contentId}
          className="flex w-full items-center justify-between px-6 py-4 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-primary-500"
        >
          <span className="flex items-center gap-2">
            {icon}
            <span className="text-sm font-bold text-slate-800 dark:text-slate-100">{title}</span>
          </span>
          <ChevronRight
            size={15}
            aria-hidden
            className={`text-slate-400 transition-transform duration-200 dark:text-slate-500 ${open ? "rotate-90 text-primary-500 dark:text-primary-400" : ""}`}
          />
        </button>
      </CardHeader>
      {open && <CardContent id={contentId}>{children}</CardContent>}
    </Card>
  );
}

const PROVIDER_OPTIONS: { value: "" | "plivo" | "twilio"; label: string }[] = [
  { value: "", label: "Automatic (prefer whichever number this org has)" },
  { value: "plivo", label: "Plivo" },
  { value: "twilio", label: "Twilio" },
];

function ProviderPreference() {
  const calling = useCallingSettings();
  const updateSettings = useUpdateCallingSettings();
  const { toast } = useToast();

  return (
    <QueryBoundary
      isLoading={calling.isLoading}
      isError={calling.isError}
      error={calling.error}
      onRetry={() => calling.refetch()}
      loadingFallback={<Skeleton className="h-20 w-full rounded-xl" />}
    >
      {calling.data && (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Which provider to try first for every outbound call this org places — a single
            dialed call, an AI callback, campaign calls, and follow-up calls. The other
            provider still stands by as a fallback if the preferred one fails.
          </p>
          <div className="max-w-xs">
            <Label htmlFor="preferred-provider">Preferred provider</Label>
            <Select
              id="preferred-provider"
              value={calling.data.preferred_provider ?? ""}
              disabled={updateSettings.isPending}
              onChange={(value) => {
                const provider = (value || null) as "plivo" | "twilio" | null;
                updateSettings.mutate(
                  { preferred_provider: provider },
                  {
                    onSuccess: () =>
                      toast({ title: "Voice provider preference saved", variant: "success" }),
                    onError: (err) =>
                      toast({
                        title: "Could not save preference",
                        description: err.message,
                        variant: "error",
                      }),
                  }
                );
              }}
            >
              {PROVIDER_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </Select>
          </div>
        </div>
      )}
    </QueryBoundary>
  );
}

/**
 * The org's default dialing prefix — added automatically to any number typed
 * or uploaded without one (calling, WhatsApp, contacts, campaigns). Only
 * affects numbers entered from now on; stored numbers are left as they are.
 */
function CountryCodeSection() {
  const countryCode = useCountryCodeSettings();
  const updateCountryCode = useUpdateCountryCodeSettings();
  const { updateUser } = useAuth();
  const { toast } = useToast();
  const current = countryCode.data?.default_country_code;
  const [draft, setDraft] = useState<string | null>(null);
  const value = draft ?? current ?? "";

  function handleSave() {
    updateCountryCode.mutate(
      { default_country_code: value },
      {
        onSuccess: (data) => {
          updateUser({ default_country_code: data.default_country_code });
          setDraft(null);
          toast({ title: "Country code saved", variant: "success" });
        },
        onError: (err) =>
          toast({
            title: "Could not save country code",
            description: err.message,
            variant: "error",
          }),
      }
    );
  }

  return (
    <QueryBoundary
      isLoading={countryCode.isLoading}
      isError={countryCode.isError}
      error={countryCode.error}
      onRetry={() => countryCode.refetch()}
      loadingFallback={<Skeleton className="h-20 w-full rounded-xl" />}
    >
      {current && (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            Added automatically to any phone number entered without a country code — in
            calling, WhatsApp, contacts and campaign uploads. Changing it only affects numbers
            entered from now on; numbers already saved stay as they are.
          </p>
          <div className="max-w-xs">
            <CountryCodeField
              id="default-country-code"
              value={value}
              onChange={setDraft}
              disabled={updateCountryCode.isPending}
            />
          </div>
          <Button
            variant="primary"
            size="sm"
            className="self-start"
            loading={updateCountryCode.isPending}
            disabled={value === current}
            onClick={handleSave}
          >
            Save country code
          </Button>
        </div>
      )}
    </QueryBoundary>
  );
}

/**
 * Picks which approved WhatsApp template the human-handoff notification
 * sends (apps/api/core/tools.py::transfer_to_human). Any active template can
 * be chosen — its variables are filled server-side: {{1}} the caller's
 * number, {{2}} the humanSupport reason, and any further {{n}} padded with the
 * reason. Empty = built-in default.
 */
function HandoffTemplatePreference() {
  const whatsapp = useWhatsAppSettings();
  const templates = useTemplates({ active: true });
  const updateSettings = useUpdateWhatsAppSettings();
  const { toast } = useToast();

  const current = whatsapp.data?.agent_connect_template_name ?? "";
  const activeTemplates = templates.data ?? [];
  // Keep a currently-saved template visible even if it's since been
  // deactivated or renamed on the Templates page.
  const options =
    current && !activeTemplates.some((t) => t.name === current)
      ? [...activeTemplates, { name: current, language: "" }]
      : activeTemplates;

  return (
    <QueryBoundary
      isLoading={whatsapp.isLoading}
      isError={whatsapp.isError}
      error={whatsapp.error}
      onRetry={() => whatsapp.refetch()}
      loadingFallback={<Skeleton className="h-20 w-full rounded-xl" />}
    >
      {whatsapp.data && (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            When the AI hands a conversation to a human, the assigned teammate gets a
            WhatsApp ping. Pick which approved template it uses. Its variables are filled
            automatically: <code className="text-xs">{"{{1}}"}</code> the caller&apos;s number,{" "}
            <code className="text-xs">{"{{2}}"}</code> the reason, and any further variables
            repeat the reason. Leave on default to use the built-in template.
          </p>
          <div className="max-w-md">
            <Label htmlFor="handoff-template">Handoff notification template</Label>
            <Select
              id="handoff-template"
              value={current}
              disabled={updateSettings.isPending || templates.isLoading}
              onChange={(value) => {
                updateSettings.mutate(
                  { agent_connect_template_name: value || null },
                  {
                    onSuccess: () =>
                      toast({ title: "Handoff template saved", variant: "success" }),
                    onError: (err) =>
                      toast({
                        title: "Could not save template",
                        description: err.message,
                        variant: "error",
                      }),
                  }
                );
              }}
            >
              <option value="">Default (built-in template)</option>
              {options.map((t) => (
                <option key={t.name} value={t.name}>
                  {t.name}
                  {t.language ? ` (${t.language})` : ""}
                </option>
              ))}
            </Select>
            {!templates.isLoading && activeTemplates.length === 0 && (
              <p className="mt-1 text-xs text-slate-400">
                No active templates yet — sync or add one on the Templates page.
              </p>
            )}
          </div>
        </div>
      )}
    </QueryBoundary>
  );
}

/**
 * Each org calls/messages through its own Plivo, Twilio, and Meta WhatsApp
 * accounts — there is no shared platform fallback (see
 * apps/api/core/org_credentials.py). These three sections let an org admin
 * view (masked) and edit its own credentials, mirroring the OpenAI-key
 * settings pattern: `configured` boolean + masked preview, edit form,
 * save/clear.
 */
function PlivoCredentialsSection() {
  const settings = usePlivoCredentialsSettings();
  const update = useUpdatePlivoCredentialsSettings();
  const del = useDeletePlivoCredentialsSettings();
  const { toast } = useToast();
  const [editing, setEditing] = useState(false);
  const [authId, setAuthId] = useState("");
  const [authToken, setAuthToken] = useState("");

  return (
    <QueryBoundary
      isLoading={settings.isLoading}
      isError={settings.isError}
      error={settings.error}
      onRetry={() => settings.refetch()}
      loadingFallback={<Skeleton className="h-20 w-full rounded-xl" />}
    >
      {settings.data && (
        <div className="flex flex-col gap-3">
          {settings.data.configured && !editing ? (
            <div className="flex flex-col gap-2">
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Auth ID: <code className="text-xs">{settings.data.auth_id}</code>
                <br />
                Auth token: <code className="text-xs">{settings.data.auth_token_preview}</code>
              </p>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
                  Edit
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  loading={del.isPending}
                  onClick={() =>
                    del.mutate(undefined, {
                      onSuccess: () => toast({ title: "Plivo credentials cleared", variant: "success" }),
                      onError: (err) =>
                        toast({ title: "Could not clear credentials", description: err.message, variant: "error" }),
                    })
                  }
                >
                  Clear
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex max-w-md flex-col gap-3">
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Found in the Plivo console under Account &gt; Auth ID / Auth Token.
              </p>
              <div>
                <Label htmlFor="plivo-settings-auth-id">Auth ID</Label>
                <Input id="plivo-settings-auth-id" value={authId} onChange={(e) => setAuthId(e.target.value)} />
              </div>
              <div>
                <Label htmlFor="plivo-settings-auth-token">Auth token</Label>
                <Input
                  id="plivo-settings-auth-token"
                  type="password"
                  value={authToken}
                  onChange={(e) => setAuthToken(e.target.value)}
                />
              </div>
              <div className="flex gap-2">
                <Button
                  variant="primary"
                  size="sm"
                  loading={update.isPending}
                  disabled={!authId.trim() || !authToken.trim()}
                  onClick={() =>
                    update.mutate(
                      { auth_id: authId.trim(), auth_token: authToken.trim() },
                      {
                        onSuccess: () => {
                          toast({ title: "Plivo credentials saved", variant: "success" });
                          setEditing(false);
                          setAuthId("");
                          setAuthToken("");
                        },
                        onError: (err) =>
                          toast({ title: "Could not save credentials", description: err.message, variant: "error" }),
                      }
                    )
                  }
                >
                  Save
                </Button>
                {settings.data.configured && (
                  <Button variant="outline" size="sm" onClick={() => setEditing(false)}>
                    Cancel
                  </Button>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </QueryBoundary>
  );
}

function TwilioCredentialsSection() {
  const settings = useTwilioCredentialsSettings();
  const update = useUpdateTwilioCredentialsSettings();
  const del = useDeleteTwilioCredentialsSettings();
  const { toast } = useToast();
  const [editing, setEditing] = useState(false);
  const [accountSid, setAccountSid] = useState("");
  const [authToken, setAuthToken] = useState("");

  return (
    <QueryBoundary
      isLoading={settings.isLoading}
      isError={settings.isError}
      error={settings.error}
      onRetry={() => settings.refetch()}
      loadingFallback={<Skeleton className="h-20 w-full rounded-xl" />}
    >
      {settings.data && (
        <div className="flex flex-col gap-3">
          {settings.data.configured && !editing ? (
            <div className="flex flex-col gap-2">
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Account SID: <code className="text-xs">{settings.data.account_sid}</code>
                <br />
                Auth token: <code className="text-xs">{settings.data.auth_token_preview}</code>
              </p>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
                  Edit
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  loading={del.isPending}
                  onClick={() =>
                    del.mutate(undefined, {
                      onSuccess: () => toast({ title: "Twilio credentials cleared", variant: "success" }),
                      onError: (err) =>
                        toast({ title: "Could not clear credentials", description: err.message, variant: "error" }),
                    })
                  }
                >
                  Clear
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex max-w-md flex-col gap-3">
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Found in the Twilio console on the Account dashboard.
              </p>
              <div>
                <Label htmlFor="twilio-settings-account-sid">Account SID</Label>
                <Input
                  id="twilio-settings-account-sid"
                  value={accountSid}
                  onChange={(e) => setAccountSid(e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="twilio-settings-auth-token">Auth token</Label>
                <Input
                  id="twilio-settings-auth-token"
                  type="password"
                  value={authToken}
                  onChange={(e) => setAuthToken(e.target.value)}
                />
              </div>
              <div className="flex gap-2">
                <Button
                  variant="primary"
                  size="sm"
                  loading={update.isPending}
                  disabled={!accountSid.trim() || !authToken.trim()}
                  onClick={() =>
                    update.mutate(
                      { account_sid: accountSid.trim(), auth_token: authToken.trim() },
                      {
                        onSuccess: () => {
                          toast({ title: "Twilio credentials saved", variant: "success" });
                          setEditing(false);
                          setAccountSid("");
                          setAuthToken("");
                        },
                        onError: (err) =>
                          toast({ title: "Could not save credentials", description: err.message, variant: "error" }),
                      }
                    )
                  }
                >
                  Save
                </Button>
                {settings.data.configured && (
                  <Button variant="outline" size="sm" onClick={() => setEditing(false)}>
                    Cancel
                  </Button>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </QueryBoundary>
  );
}

function MetaCredentialsSection() {
  const settings = useMetaCredentialsSettings();
  const update = useUpdateMetaCredentialsSettings();
  const del = useDeleteMetaCredentialsSettings();
  const { toast } = useToast();
  const [editing, setEditing] = useState(false);
  const [appId, setAppId] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [accessToken, setAccessToken] = useState("");
  const [businessAccountId, setBusinessAccountId] = useState("");
  const [verifyToken, setVerifyToken] = useState("");

  return (
    <QueryBoundary
      isLoading={settings.isLoading}
      isError={settings.isError}
      error={settings.error}
      onRetry={() => settings.refetch()}
      loadingFallback={<Skeleton className="h-20 w-full rounded-xl" />}
    >
      {settings.data && (
        <div className="flex flex-col gap-3">
          {settings.data.configured && !editing ? (
            <div className="flex flex-col gap-2">
              <p className="text-sm text-slate-500 dark:text-slate-400">
                App ID: <code className="text-xs">{settings.data.app_id}</code>
                <br />
                App secret: <code className="text-xs">{settings.data.app_secret_preview}</code>
                <br />
                Access token: <code className="text-xs">{settings.data.access_token_preview}</code>
                <br />
                WABA ID: <code className="text-xs">{settings.data.business_account_id ?? "—"}</code>
                <br />
                Webhook verify token:{" "}
                <code className="text-xs">
                  {settings.data.verify_token_configured ? settings.data.verify_token_preview : "not set"}
                </code>
              </p>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={() => setEditing(true)}>
                  Edit
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  loading={del.isPending}
                  onClick={() =>
                    del.mutate(undefined, {
                      onSuccess: () => toast({ title: "Meta credentials cleared", variant: "success" }),
                      onError: (err) =>
                        toast({ title: "Could not clear credentials", description: err.message, variant: "error" }),
                    })
                  }
                >
                  Clear
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex max-w-md flex-col gap-3">
              <p className="text-sm text-slate-500 dark:text-slate-400">
                Found in Meta App dashboard &gt; App settings &gt; Basic, and WhatsApp &gt; API
                Setup. The verify token is whatever you set when registering this webhook URL in
                the App&apos;s WhatsApp &gt; Configuration screen.
              </p>
              <div>
                <Label htmlFor="meta-settings-app-id">App ID</Label>
                <Input id="meta-settings-app-id" value={appId} onChange={(e) => setAppId(e.target.value)} />
              </div>
              <div>
                <Label htmlFor="meta-settings-app-secret">App secret</Label>
                <Input
                  id="meta-settings-app-secret"
                  type="password"
                  value={appSecret}
                  onChange={(e) => setAppSecret(e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="meta-settings-access-token">Access token</Label>
                <Input
                  id="meta-settings-access-token"
                  type="password"
                  value={accessToken}
                  onChange={(e) => setAccessToken(e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="meta-settings-waba-id">WhatsApp Business Account ID</Label>
                <Input
                  id="meta-settings-waba-id"
                  value={businessAccountId}
                  onChange={(e) => setBusinessAccountId(e.target.value)}
                />
              </div>
              <div>
                <Label htmlFor="meta-settings-verify-token">Webhook verify token</Label>
                <Input
                  id="meta-settings-verify-token"
                  type="password"
                  value={verifyToken}
                  onChange={(e) => setVerifyToken(e.target.value)}
                />
              </div>
              <div className="flex gap-2">
                <Button
                  variant="primary"
                  size="sm"
                  loading={update.isPending}
                  disabled={!appId.trim() || !appSecret.trim() || !accessToken.trim()}
                  onClick={() =>
                    update.mutate(
                      {
                        app_id: appId.trim(),
                        app_secret: appSecret.trim(),
                        access_token: accessToken.trim(),
                        business_account_id: businessAccountId.trim() || null,
                        verify_token: verifyToken.trim() || null,
                      },
                      {
                        onSuccess: () => {
                          toast({ title: "Meta credentials saved", variant: "success" });
                          setEditing(false);
                          setAppId("");
                          setAppSecret("");
                          setAccessToken("");
                          setBusinessAccountId("");
                          setVerifyToken("");
                        },
                        onError: (err) =>
                          toast({ title: "Could not save credentials", description: err.message, variant: "error" }),
                      }
                    )
                  }
                >
                  Save
                </Button>
                {settings.data.configured && (
                  <Button variant="outline" size="sm" onClick={() => setEditing(false)}>
                    Cancel
                  </Button>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </QueryBoundary>
  );
}

export interface SettingsViewProps {
  title: string;
  description: string;
  /** Which channel this page is for — selects which number field to show. */
  channel: "whatsapp" | "calling";
}

/**
 * Editable script + number view for the per-channel /whatsapp/settings and
 * /calling/settings pages. Both now manage a full per-channel script
 * library (see ./script-library.tsx, core/agent.py::_system_prompt_for for
 * WhatsApp and channels/voice/realtime_bridge.py::_system_instructions for
 * calling). The number is channel-specific either way and determines which
 * org an inbound message/call on it resolves to
 * (channels/whatsapp/adapter.py, channels/voice/webhook.py).
 */
export function SettingsView({ title, description, channel }: SettingsViewProps) {
  const { user } = useAuth();
  // Provider preference is centralized with the org admin — a plain
  // role=="member" account doesn't get to see this section exists, let alone
  // change it (mirrors the 403 guard on GET/PUT /admin/settings/calling).
  const isOrgAdmin = user?.is_superuser || user?.role !== "member";

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader title={title} description={description} />

      <div className="flex flex-col gap-5">
        <CollapsibleSection
          title="Script"
          icon={<Bot size={15} aria-hidden className="text-slate-400" />}
          defaultOpen
          maxWidthClassName="max-w-5xl"
        >
          <ScriptLibrary channel={channel === "calling" ? "voice" : "whatsapp"} />
        </CollapsibleSection>

        {channel === "whatsapp" && (
          <CollapsibleSection
            title="Human Handoff"
            icon={<Users size={15} aria-hidden className="text-slate-400" />}
          >
            <HandoffTemplatePreference />
          </CollapsibleSection>
        )}

        {isOrgAdmin && (
          <CollapsibleSection
            title="Country Code"
            icon={<Globe size={15} aria-hidden className="text-slate-400" />}
            defaultOpen
          >
            <CountryCodeSection />
          </CollapsibleSection>
        )}

        {channel === "whatsapp" && isOrgAdmin && (
          <CollapsibleSection
            title="Meta WhatsApp Credentials"
            icon={<KeyRound size={15} aria-hidden className="text-slate-400" />}
          >
            <MetaCredentialsSection />
          </CollapsibleSection>
        )}

        {channel === "calling" && isOrgAdmin && (
          <CollapsibleSection
            title="Voice Provider"
            icon={<Phone size={15} aria-hidden className="text-slate-400" />}
            defaultOpen
          >
            <ProviderPreference />
          </CollapsibleSection>
        )}

        {channel === "calling" && isOrgAdmin && (
          <CollapsibleSection
            title="Plivo Credentials"
            icon={<KeyRound size={15} aria-hidden className="text-slate-400" />}
          >
            <PlivoCredentialsSection />
          </CollapsibleSection>
        )}

        {channel === "calling" && isOrgAdmin && (
          <CollapsibleSection
            title="Twilio Credentials"
            icon={<KeyRound size={15} aria-hidden className="text-slate-400" />}
          >
            <TwilioCredentialsSection />
          </CollapsibleSection>
        )}
      </div>
    </div>
  );
}
