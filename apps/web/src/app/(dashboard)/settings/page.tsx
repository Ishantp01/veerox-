"use client";

import { useState } from "react";
import { Phone, MessageSquare } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { SettingsView } from "@/components/settings/settings-view";

type Tab = "calling" | "whatsapp";

const TABS: { key: Tab; label: string; icon: typeof Phone }[] = [
  { key: "calling", label: "AI Calling", icon: Phone },
  { key: "whatsapp", label: "AI WhatsApp", icon: MessageSquare },
];

/**
 * Sidebar-level Settings entry point. Merges the two per-channel settings
 * pages (still separately reachable via each channel's section tabs at
 * /calling/settings and /whatsapp/settings) behind one tabbed view so the
 * grouped sidebar's Settings item isn't a dead stub.
 */
export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("calling");

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Settings" description="Connection status, prompts, and tools for each agent channel." />

      <div className="mb-6 inline-flex gap-1 rounded-xl border border-slate-200 bg-white p-1 dark:border-slate-800 dark:bg-slate-900">
        {TABS.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`flex items-center gap-2 rounded-lg px-3.5 py-1.5 text-sm font-semibold transition-colors ${
              tab === key
                ? "bg-primary-500 text-white shadow-sm"
                : "text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-100"
            }`}
          >
            <Icon size={15} aria-hidden />
            {label}
          </button>
        ))}
      </div>

      {tab === "calling" ? (
        <SettingsView
          title="Calling Settings"
          description="Connection status, active prompts, and registered tools for the AI Calling agent."
          promptKeys={["base", "voice_append"]}
          channel="calling"
        />
      ) : (
        <SettingsView
          title="WhatsApp Settings"
          description="Connection status, active prompts, and registered tools for the WhatsApp agent."
          promptKeys={["base", "whatsapp_append"]}
          channel="whatsapp"
        />
      )}
    </div>
  );
}
