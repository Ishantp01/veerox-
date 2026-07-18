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
    chip: "from-emerald-400 to-emerald-600",
  },
  {
    href: "/calling",
    label: "AI Calling Agent",
    description: "Conversations, leads, escalations, and settings for the voice channel.",
    Icon: Phone,
    chip: "from-primary-400 to-primary-600",
  },
  {
    href: "/reports",
    label: "Reports",
    description: "Trends across both channels and per-campaign qualification rates for the sales team.",
    Icon: BarChart3,
    chip: "from-amber-400 to-amber-600",
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
        {SECTIONS.map(({ href, label, description, Icon, chip }) => (
          <Link
            key={href}
            href={href}
            className="group flex items-start gap-4 rounded-2xl border border-slate-200/80 bg-white p-6 shadow-card transition-all duration-200 hover:-translate-y-0.5 hover:shadow-card-lg focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 dark:border-slate-800 dark:bg-slate-900"
          >
            <div className={`flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br text-white shadow-sm transition-transform duration-200 group-hover:scale-105 ${chip}`}>
              <Icon size={18} aria-hidden />
            </div>
            <div className="flex-1">
              <p className="flex items-center gap-1.5 text-sm font-semibold text-slate-800 dark:text-slate-100">
                {label}
                <ArrowRight
                  size={14}
                  aria-hidden
                  className="text-slate-300 transition-transform group-hover:translate-x-0.5 group-hover:text-primary-500 dark:text-slate-600"
                />
              </p>
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{description}</p>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}
