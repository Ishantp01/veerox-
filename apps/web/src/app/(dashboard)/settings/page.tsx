"use client";

import { useState } from "react";
import { Phone, MessageSquare } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { SettingsView } from "@/components/settings/settings-view";
import { useAuth } from "@/lib/auth-context";

type Tab = "calling" | "whatsapp";

const TABS: { key: Tab; label: string; icon: typeof Phone }[] = [
  { key: "calling", label: "AI Calling", icon: Phone },
  { key: "whatsapp", label: "AI WhatsApp", icon: MessageSquare },
];

// The superuser-only "Help Desk Bot" and "Social Links" tabs (PLATFORM_TABS)
// were removed from here — see removefeature.md to re-add.

/**
 * Sidebar-level Settings entry point. Merges the two per-channel settings
 * pages (still separately reachable via each channel's section tabs at
 * /calling/settings and /whatsapp/settings) behind one tabbed view so the
 * grouped sidebar's Settings item isn't a dead stub.
 */
export default function SettingsPage() {
  const [tab, setTab] = useState<Tab>("calling");
  const { user } = useAuth();

  return (
    <div className="mx-auto max-w-5xl">
      <PageHeader title="Settings" description="The script each agent channel follows." />

      {user && (
        <div className="mb-6 rounded-2xl border border-slate-200/80 bg-white p-4 shadow-card dark:border-slate-800 dark:bg-slate-900">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400 dark:text-slate-500">
            Account
          </h2>
          <dl className="mt-2 grid grid-cols-1 gap-x-6 gap-y-2 sm:grid-cols-3">
            <div>
              <dt className="text-xs text-slate-500 dark:text-slate-400">Organization</dt>
              <dd className="text-sm font-semibold text-slate-800 dark:text-slate-100">{user.org_name}</dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500 dark:text-slate-400">Organization ID</dt>
              <dd className="font-mono text-xs text-slate-600 dark:text-slate-400">{user.org_id}</dd>
            </div>
            <div>
              <dt className="text-xs text-slate-500 dark:text-slate-400">Signed in as</dt>
              <dd className="text-sm text-slate-700 dark:text-slate-300">
                {user.email}
                <span className="ml-1.5 text-xs text-slate-400 dark:text-slate-500">({user.role})</span>
              </dd>
            </div>
          </dl>
        </div>
      )}

      <div
        data-tour="settings-channel-tabs"
        className="mb-6 inline-flex gap-1 rounded-xl border border-slate-200 bg-white p-1 dark:border-slate-800 dark:bg-slate-900"
      >
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

      {tab === "calling" && (
        <SettingsView
          title="Calling Settings"
          description="The script your AI Calling agent follows."
          channel="calling"
        />
      )}
      {tab === "whatsapp" && (
        <SettingsView
          title="WhatsApp Settings"
          description="The script your WhatsApp agent follows."
          channel="whatsapp"
        />
      )}
    </div>
  );
}
