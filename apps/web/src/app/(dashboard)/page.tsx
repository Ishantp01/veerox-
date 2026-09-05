"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { MessageSquare, Phone, BarChart3, ArrowRight, CalendarDays } from "lucide-react";
import { KillSwitchBanner } from "@/components/dashboard/kill-switch-banner";
import { StatsGrid } from "@/components/dashboard/stats-grid";
import { useToast } from "@/components/ui";
import { useKillSwitch, useSetKillSwitch } from "@/lib/hooks";
import { TourTrigger } from "@/components/onboarding/tour-trigger";
import { ONBORDA_IDS } from "@/lib/onboarding/tours";

const SECTIONS = [
  {
    href: "/whatsapp",
    label: "WhatsApp AI Agent",
    description: "Send an outbound WhatsApp message and see channel analytics.",
    Icon: MessageSquare,
    chip: "from-emerald-400 to-emerald-600",
  },
  {
    href: "/calling",
    label: "AI Calling Agent",
    description: "Place an outbound call and see channel analytics.",
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

function useGreeting(): string {
  const [greeting, setGreeting] = useState("Welcome back");
  useEffect(() => {
    const hour = new Date().getHours();
    setGreeting(hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening");
  }, []);
  return greeting;
}

export default function LandingPage() {
  const killSwitch = useKillSwitch();
  const setKillSwitch = useSetKillSwitch();
  const { toast } = useToast();
  const greeting = useGreeting();
  const today = new Date().toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });

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
      <div className="mb-8 flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
        <div id={ONBORDA_IDS.dashboardWelcome}>
          <h1 className="flex items-center gap-2 text-xl font-bold text-slate-900 dark:text-white">
            {greeting}! <span className="text-2xl">👋</span>
          </h1>
          <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
            Real-time overview across both agent channels — the kill switch below pauses both at once.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <TourTrigger />
          <div className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white px-4 py-2 text-xs font-medium text-slate-600 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300">
            <span>{today}</span>
            <CalendarDays size={14} aria-hidden />
          </div>
        </div>
      </div>

      {/* Kill-switch control reflects server state once loaded. It's global
          by design: there's a single agent pause flag, not one per channel. */}
      <div id={ONBORDA_IDS.killSwitch}>
        {!killSwitch.isLoading && !killSwitch.isError && (
          <KillSwitchBanner
            enabled={enabled}
            loading={setKillSwitch.isPending}
            onToggle={handleToggle}
          />
        )}
      </div>

      <div className="flex flex-col gap-8">
        <div id={ONBORDA_IDS.stats}>
          <StatsGrid variant="all" />
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
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
    </div>
  );
}
