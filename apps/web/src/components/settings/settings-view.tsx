"use client";

import { useEffect, useState, type ReactNode } from "react";
import { Bot, ChevronRight, Phone } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  Label,
  Select,
  Skeleton,
  Textarea,
  useToast,
} from "@/components/ui";
import { useCallingSettings, useScript, useUpdateCallingSettings, useUpdateScript } from "@/lib/hooks";

interface CollapsibleSectionProps {
  title: string;
  icon: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
}

function CollapsibleSection({
  title,
  icon,
  defaultOpen = false,
  children,
}: CollapsibleSectionProps) {
  const [open, setOpen] = useState(defaultOpen);
  const contentId = `section-${title.replace(/\s+/g, "-").toLowerCase()}`;
  return (
    <Card className="max-w-3xl">
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

function ScriptEditor() {
  const script = useScript();
  const updateScript = useUpdateScript();
  const [draft, setDraft] = useState("");
  const [editing, setEditing] = useState(false);

  useEffect(() => {
    if (script.data) setDraft(script.data.script);
  }, [script.data]);

  const dirty = script.data !== undefined && draft !== script.data.script;

  return (
    <QueryBoundary
      isLoading={script.isLoading}
      isError={script.isError}
      error={script.error}
      onRetry={() => script.refetch()}
      loadingFallback={<Skeleton className="h-48 w-full rounded-xl" />}
    >
      {script.data && (
        <div className="flex flex-col gap-3">
          <p className="text-sm text-slate-500 dark:text-slate-400">
            This is what the AI follows on both WhatsApp and calls. It can still answer
            questions outside this script — this just sets the flow it returns to.
            {script.data.is_default && " Currently using the platform default shown below."}
          </p>
          <Textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            readOnly={!editing}
            rows={18}
            className={`font-mono text-xs ${!editing ? "cursor-default bg-slate-50 dark:bg-slate-800/50" : ""}`}
          />
          <div className="flex items-center gap-2">
            {!editing ? (
              <Button size="sm" variant="outline" onClick={() => setEditing(true)}>
                Edit
              </Button>
            ) : (
              <>
                <Button
                  size="sm"
                  disabled={!dirty}
                  loading={updateScript.isPending}
                  onClick={() => {
                    updateScript.mutate(draft);
                    setEditing(false);
                  }}
                >
                  Save script
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  disabled={script.data.is_default || updateScript.isPending}
                  onClick={() => {
                    updateScript.mutate(null);
                    setEditing(false);
                  }}
                >
                  Reset to default
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={updateScript.isPending}
                  onClick={() => {
                    setDraft(script.data!.script);
                    setEditing(false);
                  }}
                >
                  Cancel
                </Button>
              </>
            )}
            {updateScript.isError && (
              <span className="text-xs text-red-500">{updateScript.error.message}</span>
            )}
          </div>
        </div>
      )}
    </QueryBoundary>
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

export interface SettingsViewProps {
  title: string;
  description: string;
  /** Which channel this page is for — selects which number field to show. */
  channel: "whatsapp" | "calling";
}

/**
 * Editable script + number view for the per-channel /whatsapp/settings and
 * /calling/settings pages. The script is shared by both channels (see
 * core/agent.py::_system_prompt_for and
 * channels/voice/realtime_bridge.py::_system_instructions); the number is
 * channel-specific and determines which org an inbound message/call on it
 * resolves to (channels/whatsapp/adapter.py, channels/voice/webhook.py).
 */
export function SettingsView({ title, description, channel }: SettingsViewProps) {
  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader title={title} description={description} />

      <div className="flex flex-col gap-5">
        <CollapsibleSection
          title="Script"
          icon={<Bot size={15} aria-hidden className="text-slate-400" />}
          defaultOpen
        >
          <ScriptEditor />
        </CollapsibleSection>

        {channel === "calling" && (
          <CollapsibleSection
            title="Voice Provider"
            icon={<Phone size={15} aria-hidden className="text-slate-400" />}
            defaultOpen
          >
            <ProviderPreference />
          </CollapsibleSection>
        )}
      </div>
    </div>
  );
}
