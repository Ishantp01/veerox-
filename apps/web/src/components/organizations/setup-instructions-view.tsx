"use client";

import { Copy } from "lucide-react";
import { Button, Label, useToast } from "@/components/ui";
import type { SetupInstructions } from "@/lib/hooks/usePlatformOrgs";

/**
 * The one-time "hosting setup" screen (shown right after a deployment
 * token is generated — organization creation, preparing setup for a
 * pre-existing org, or replacing a lost token). This is the ONLY place the
 * raw token is ever shown; only its hash is stored server-side, so it
 * cannot be displayed again after this.
 */
export function SetupInstructionsView({ setup }: { setup: SetupInstructions }) {
  const { toast } = useToast();

  function copy(label: string, text: string) {
    navigator.clipboard
      .writeText(text)
      .then(() => toast({ title: `${label} copied`, variant: "success" }))
      .catch(() => toast({ title: "Couldn't copy — select and copy manually", variant: "error" }));
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 ring-1 ring-inset ring-amber-200 dark:bg-amber-500/10 dark:text-amber-300 dark:ring-amber-500/20">
        This is shown <strong>once</strong>. Save it now — it cannot be shown again. If it&apos;s lost
        later, you can generate a replacement, but the old one stops working immediately.
      </div>

      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <Label className="mb-0">Configuration for the client&apos;s server</Label>
          <Button variant="ghost" size="sm" onClick={() => copy("Configuration", setup.env_snippet)}>
            <Copy size={13} aria-hidden />
            Copy all
          </Button>
        </div>
        <pre className="overflow-x-auto whitespace-pre-wrap break-all rounded-lg bg-slate-100 px-3 py-2 text-xs dark:bg-slate-800">
          {setup.env_snippet}
        </pre>
        <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
          Give this to whoever sets up the client&apos;s server — it goes into that server&apos;s own
          configuration file. Replace the database line with the client&apos;s real database
          connection before using it.
        </p>
      </div>

      <div>
        <div className="mb-1.5 flex items-center justify-between">
          <Label className="mb-0">Setup token only</Label>
          <Button variant="ghost" size="sm" onClick={() => copy("Token", setup.deployment_token)}>
            <Copy size={13} aria-hidden />
            Copy
          </Button>
        </div>
        <code className="block break-all rounded-lg bg-slate-100 px-3 py-2 text-xs dark:bg-slate-800">
          {setup.deployment_token}
        </code>
      </div>
    </div>
  );
}
