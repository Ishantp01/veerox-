"use client";

import { useState, type ReactNode } from "react";
import { Bot, ChevronRight, Plug, Wrench } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { QueryBoundary } from "@/components/layout/query-boundary";
import { Badge, Card, CardContent, CardHeader, Skeleton } from "@/components/ui";
import { usePrompts, useTools, useWhatsAppSettings, useCallingSettings } from "@/lib/hooks";
import type { CallingSettings, Prompts, Tool, WhatsAppSettings } from "@/lib/types";

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

function CodeBlock({ children }: { children: ReactNode }) {
  return (
    <pre className="overflow-x-auto rounded-xl border border-slate-700 bg-slate-900 p-4 font-mono text-xs leading-relaxed whitespace-pre-wrap break-words text-slate-100">
      {children}
    </pre>
  );
}

function PromptsBlocks({
  prompts,
  promptKeys,
}: {
  prompts: Prompts;
  promptKeys: Array<keyof Prompts>;
}) {
  return (
    <div className="flex flex-col gap-4">
      {promptKeys.map((key) => (
        <div key={key}>
          <p className="mb-1.5 text-[11px] font-bold uppercase tracking-widest text-slate-400">
            {key}
          </p>
          <CodeBlock>{prompts[key] || "—"}</CodeBlock>
        </div>
      ))}
    </div>
  );
}

function ToolsBlocks({ tools }: { tools: Tool[] }) {
  return (
    <div className="flex flex-col gap-4">
      {tools.map((tool, idx) => {
        const name =
          tool.function?.name ??
          (typeof tool.name === "string" ? tool.name : `tool_${idx}`);
        return (
          <div key={`${name}_${idx}`}>
            <p className="mb-1.5 text-[11px] font-bold uppercase tracking-widest text-primary-500">
              {name}
            </p>
            <CodeBlock>{JSON.stringify(tool, null, 2)}</CodeBlock>
          </div>
        );
      })}
    </div>
  );
}

function ConfiguredBadge({ ok }: { ok: boolean }) {
  return ok ? <Badge variant="success">Configured</Badge> : <Badge variant="danger">Not set</Badge>;
}

function StatusRow({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-slate-100 py-2.5 last:border-0 dark:border-slate-800">
      <span className="text-sm text-slate-600 dark:text-slate-400">{label}</span>
      <span className="text-right text-sm font-medium break-all text-slate-800 dark:text-slate-200">{value}</span>
    </div>
  );
}

function WhatsAppConnectionStatus({ settings }: { settings: WhatsAppSettings }) {
  return (
    <div className="flex flex-col">
      <StatusRow label="Access token" value={<ConfiguredBadge ok={settings.access_token_configured} />} />
      <StatusRow label="Phone number ID" value={settings.phone_number_id ?? "—"} />
      <StatusRow
        label="Business account ID"
        value={settings.whatsapp_business_account_id ?? "—"}
      />
      <StatusRow label="App ID" value={<ConfiguredBadge ok={settings.app_id_configured} />} />
      <StatusRow label="App secret" value={<ConfiguredBadge ok={settings.app_secret_configured} />} />
      <StatusRow
        label="Webhook verify token"
        value={<ConfiguredBadge ok={settings.verify_token_configured} />}
      />
      <StatusRow label="Graph API version" value={settings.graph_api_version} />
      <StatusRow label="Webhook callback URL" value={settings.webhook_url} />
    </div>
  );
}

function CallingConnectionStatus({ settings }: { settings: CallingSettings }) {
  return (
    <div className="flex flex-col">
      <StatusRow label="Auth ID" value={<ConfiguredBadge ok={settings.auth_id_configured} />} />
      <StatusRow label="Auth token" value={<ConfiguredBadge ok={settings.auth_token_configured} />} />
      <StatusRow label="Phone number" value={settings.phone_number ?? "—"} />
      <StatusRow label="Answer webhook URL" value={settings.answer_webhook_url} />
    </div>
  );
}

export interface SettingsViewProps {
  title: string;
  description: string;
  /** Which prompt blocks to show, in order — e.g. ["base", "whatsapp_append"]. */
  promptKeys: Array<keyof Prompts>;
  /** Which channel's connection status to show above the prompts/tools. */
  channel: "whatsapp" | "calling";
}

/**
 * Read-only connection status + prompts + tools view. Used by the
 * per-channel /whatsapp/settings and /calling/settings pages — `channel`
 * selects which channel's config status to fetch, `promptKeys` selects
 * which prompt blocks are relevant.
 */
export function SettingsView({ title, description, promptKeys, channel }: SettingsViewProps) {
  const prompts = usePrompts();
  const tools = useTools();
  const whatsappSettings = useWhatsAppSettings();
  const callingSettings = useCallingSettings();
  const connection = channel === "whatsapp" ? whatsappSettings : callingSettings;

  const loadingFallback = <Skeleton className="h-24 w-full rounded-xl" />;

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader title={title} description={description} />

      <div className="flex flex-col gap-5">
        <CollapsibleSection
          title="Connection Status"
          icon={<Plug size={15} aria-hidden className="text-slate-400" />}
          defaultOpen
        >
          <QueryBoundary
            isLoading={connection.isLoading}
            isError={connection.isError}
            error={connection.error}
            onRetry={() => connection.refetch()}
            loadingFallback={loadingFallback}
          >
            {channel === "whatsapp" && whatsappSettings.data && (
              <WhatsAppConnectionStatus settings={whatsappSettings.data} />
            )}
            {channel === "calling" && callingSettings.data && (
              <CallingConnectionStatus settings={callingSettings.data} />
            )}
          </QueryBoundary>
        </CollapsibleSection>

        <CollapsibleSection
          title="Active Prompts"
          icon={<Bot size={15} aria-hidden className="text-slate-400" />}
        >
          <QueryBoundary
            isLoading={prompts.isLoading}
            isError={prompts.isError}
            error={prompts.error}
            onRetry={() => prompts.refetch()}
            loadingFallback={loadingFallback}
          >
            {prompts.data && <PromptsBlocks prompts={prompts.data} promptKeys={promptKeys} />}
          </QueryBoundary>
        </CollapsibleSection>

        <CollapsibleSection
          title="Registered Tools"
          icon={<Wrench size={15} aria-hidden className="text-slate-400" />}
        >
          <QueryBoundary
            isLoading={tools.isLoading}
            isError={tools.isError}
            error={tools.error}
            isEmpty={(tools.data?.length ?? 0) === 0}
            onRetry={() => tools.refetch()}
            loadingFallback={loadingFallback}
            emptyFallback={<p className="text-sm text-slate-500">No tools registered.</p>}
          >
            {tools.data && <ToolsBlocks tools={tools.data} />}
          </QueryBoundary>
        </CollapsibleSection>
      </div>
    </div>
  );
}
