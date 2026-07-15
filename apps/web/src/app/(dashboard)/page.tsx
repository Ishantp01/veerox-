"use client";

import Link from "next/link";
import { MessageSquare, Phone, BarChart3, ArrowRight } from "lucide-react";
import { PageHeader } from "@/components/layout/page-header";
import { KillSwitchBanner } from "@/components/dashboard/kill-switch-banner";
import { StatsGrid } from "@/components/dashboard/stats-grid";
import { useToast } from "@/components/ui";
import { useKillSwitch, useSetKillSwitch } from "@/lib/hooks";

const SECTIONS = [
  {
    href: "/whatsapp",
    label: "WhatsApp AI Agent",
    description: "Conversations, leads, escalations, and settings for the WhatsApp channel.",
    Icon: MessageSquare,
    accent: "bg-emerald-600 shadow-emerald-200",
  },
  {
    href: "/calling",
    label: "AI Calling Agent",
    description: "Conversations, leads, escalations, and settings for the voice channel.",
    Icon: Phone,
    accent: "bg-indigo-600 shadow-indigo-200",
  },
  {
    href: "/reports",
    label: "Reports",
    description: "Trends across both channels and per-campaign qualification rates for the sales team.",
    Icon: BarChart3,
    accent: "bg-amber-600 shadow-amber-200",
  },
] as const;

export default function LandingPage() {
  const killSwitch = useKillSwitch();
  const setKillSwitch = useSetKillSwitch();
  const { toast } = useToast();

  const enabled = killSwitch.data?.enabled ?? false;

  function handleToggle(next: boolean) {
    setKillSwitch.mutate(next, {
      onSuccess: () =>
        toast({
          title: next ? "Agent paused" : "Agent resumed",
          description: next
            ? "Incoming messages now receive a hold response."
            : "The agent is answering messages again.",
          variant: next ? "info" : "success",
        }),
      onError: (err) =>
        toast({
          title: "Could not update the kill switch",
          description: err.message,
          variant: "error",
        }),
    });
  }

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Dashboard"
        description="Real-time overview across both agent channels — the kill switch below pauses both at once."
      />

      {/* Kill-switch control reflects server state once loaded. It's global
          by design: there's a single agent pause flag, not one per channel. */}
      {!killSwitch.isLoading && !killSwitch.isError && (
        <KillSwitchBanner
          enabled={enabled}
          loading={setKillSwitch.isPending}
          onToggle={handleToggle}
        />
      )}

      <StatsGrid variant="all" />

      <div className="mt-8 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {SECTIONS.map(({ href, label, description, Icon, accent }) => (
          <Link
            key={href}
            href={href}
            className="group flex items-start gap-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm transition-all hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
          >
            <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl text-white shadow-sm ${accent}`}>
              <Icon size={18} aria-hidden />
            </div>
            <div className="flex-1">
              <p className="flex items-center gap-1.5 text-sm font-bold text-slate-800">
                {label}
                <ArrowRight
                  size={14}
                  aria-hidden
                  className="text-slate-300 transition-transform group-hover:translate-x-0.5 group-hover:text-slate-500"
                />
              </p>
              <p className="mt-1 text-xs text-slate-500">{description}</p>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
