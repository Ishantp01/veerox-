"use client";

import { useEffect, useState } from "react";

import { Button, Card, CardContent, CardHeader, Input, Textarea, useToast } from "@/components/ui";
import { usePlatformSettings, useUpdatePlatformSettings } from "@/lib/hooks";

// Fixed, known social platforms — stored as flat keys inside
// PlatformSettings.social_links so the backend stays schema-free.
const SOCIAL_FIELDS: { key: string; label: string; placeholder: string }[] = [
  { key: "twitter", label: "Twitter / X", placeholder: "https://x.com/veerox" },
  { key: "linkedin", label: "LinkedIn", placeholder: "https://linkedin.com/company/veerox" },
  { key: "instagram", label: "Instagram", placeholder: "https://instagram.com/veerox" },
  { key: "facebook", label: "Facebook", placeholder: "https://facebook.com/veerox" },
  { key: "youtube", label: "YouTube", placeholder: "https://youtube.com/@veerox" },
  { key: "whatsapp", label: "WhatsApp", placeholder: "https://wa.me/919999999999" },
  { key: "website", label: "Website", placeholder: "https://veerox.ai" },
];

/**
 * Platform-wide help-desk chatbot script editor — superuser-only, same
 * PlatformAdminDep gate as the Billing page's plan catalog
 * (apps/api/routers/billing.py's /billing/platform-settings endpoints).
 */
export function HelpDeskScriptPanel() {
  const settings = usePlatformSettings();
  const updateSettings = useUpdatePlatformSettings();
  const { toast } = useToast();

  const [scriptDraft, setScriptDraft] = useState("");

  useEffect(() => {
    if (settings.data) setScriptDraft(settings.data.help_desk_script ?? "");
  }, [settings.data]);

  if (settings.isLoading || !settings.data) return null;

  const scriptDirty = scriptDraft !== (settings.data.help_desk_script ?? "");

  function saveScript() {
    updateSettings.mutate(
      { help_desk_script: scriptDraft.trim() || null },
      {
        onSuccess: () => toast({ title: "Help-desk script saved", variant: "success" }),
        onError: (err) =>
          toast({ title: "Could not save script", description: err.message, variant: "error" }),
      }
    );
  }

  return (
    <Card className="max-w-3xl">
      <CardHeader>
        <h3 className="text-sm font-bold text-slate-800 dark:text-slate-100">
          Help-desk chatbot script
        </h3>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          System prompt for the in-app help-desk widget every org&apos;s team members can open.
          It&apos;s global — there is no per-org override. Leave blank to use the built-in default
          (scoped to Veerox product questions only).
        </p>
        <Textarea
          value={scriptDraft}
          onChange={(e) => setScriptDraft(e.target.value)}
          rows={12}
          placeholder="Leave blank to use the built-in default"
          className="font-mono text-xs"
        />
        <div className="flex items-center gap-2">
          <Button size="sm" disabled={!scriptDirty} loading={updateSettings.isPending} onClick={saveScript}>
            Save script
          </Button>
          {settings.data.help_desk_script && (
            <Button
              size="sm"
              variant="outline"
              disabled={updateSettings.isPending}
              onClick={() => setScriptDraft("")}
            >
              Clear (use default)
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

/**
 * Platform-wide social links editor — superuser-only, same gate as
 * HelpDeskScriptPanel above. Shown to every client org (e.g. nav footer).
 */
export function SocialLinksPanel() {
  const settings = usePlatformSettings();
  const updateSettings = useUpdatePlatformSettings();
  const { toast } = useToast();

  const [linksDraft, setLinksDraft] = useState<Record<string, string>>({});

  useEffect(() => {
    if (settings.data) setLinksDraft(settings.data.social_links);
  }, [settings.data]);

  if (settings.isLoading || !settings.data) return null;

  const linksDirty = JSON.stringify(linksDraft) !== JSON.stringify(settings.data.social_links);

  function saveLinks() {
    updateSettings.mutate(
      { social_links: linksDraft },
      {
        onSuccess: () => toast({ title: "Social links saved", variant: "success" }),
        onError: (err) =>
          toast({ title: "Could not save social links", description: err.message, variant: "error" }),
      }
    );
  }

  return (
    <Card className="max-w-3xl">
      <CardHeader>
        <h3 className="text-sm font-bold text-slate-800 dark:text-slate-100">Social links</h3>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <p className="text-sm text-slate-500 dark:text-slate-400">
          Shown to every client org (e.g. in the sidebar). Leave a field blank to hide it.
        </p>
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {SOCIAL_FIELDS.map(({ key, label, placeholder }) => (
            <div key={key} className="flex flex-col gap-1.5">
              <span className="text-[11px] font-bold uppercase tracking-widest text-slate-400">
                {label}
              </span>
              <Input
                value={linksDraft[key] ?? ""}
                onChange={(e) => setLinksDraft((prev) => ({ ...prev, [key]: e.target.value }))}
                placeholder={placeholder}
              />
            </div>
          ))}
        </div>
        <div>
          <Button size="sm" disabled={!linksDirty} loading={updateSettings.isPending} onClick={saveLinks}>
            Save social links
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/** Both panels together — used where a single combined section still makes sense (e.g. Billing page). */
export function PlatformSettingsPanel() {
  return (
    <div className="flex flex-col gap-5">
      <HelpDeskScriptPanel />
      <SocialLinksPanel />
    </div>
  );
}
