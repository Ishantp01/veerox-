"use client";

import { type LucideIcon, MessageSquareText, PhoneCall, Power } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle, Skeleton } from "@/components/ui";
import { useCallingSettings, useKillSwitch, useWhatsAppSettings } from "@/lib/hooks";

interface Row {
  label: string;
  ok: boolean;
  statusText: string;
  Icon: LucideIcon;
  iconClassName: string;
}

/**
 * Live channel health at a glance — WhatsApp/Plivo config status and the
 * global kill switch, the only "is the agent actually working" signals the
 * API exposes today. Deliberately backed by real config/kill-switch reads
 * rather than a synthetic uptime feed, so it never claims a service is
 * healthy when it isn't configured.
 */
export function SystemStatus() {
  const whatsapp = useWhatsAppSettings();
  const calling = useCallingSettings();
  const killSwitch = useKillSwitch();

  const isLoading = whatsapp.isLoading || calling.isLoading || killSwitch.isLoading;

  const rows: Row[] = [
    {
      label: "WhatsApp API",
      ok: whatsapp.data?.configured ?? false,
      statusText: whatsapp.data?.configured ? "Connected" : "Not configured",
      Icon: MessageSquareText,
      iconClassName: "bg-green-500/10 text-green-500",
    },
    {
      label: "AI Calling Agent",
      ok: calling.data?.configured ?? false,
      statusText: calling.data?.configured ? "Connected" : "Not configured",
      Icon: PhoneCall,
      iconClassName: "bg-purple-500/10 text-purple-500",
    },
    {
      label: "Agent Kill Switch",
      ok: !(killSwitch.data?.enabled ?? false),
      statusText: killSwitch.data?.enabled ? "Paused" : "Active",
      Icon: Power,
      iconClassName: "bg-primary-500/10 text-primary-500",
    },
  ];

  const allOk = rows.every((r) => r.ok);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>System Status</CardTitle>
        {!isLoading && (
          <span className="text-[10px] text-slate-400 dark:text-slate-500">
            {allOk ? "All systems operational" : "Attention needed"}
          </span>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        {isLoading
          ? Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-14 w-full" />)
          : rows.map(({ label, ok, statusText, Icon, iconClassName }) => (
              <div
                key={label}
                className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50 p-3 dark:border-slate-700 dark:bg-slate-900"
              >
                <div className="flex items-center gap-3">
                  <div className={`flex h-8 w-8 items-center justify-center rounded-lg ${iconClassName}`}>
                    <Icon size={14} aria-hidden />
                  </div>
                  <div>
                    <p className="text-xs font-medium text-slate-800 dark:text-slate-100">{label}</p>
                    <p className={`text-[9px] ${ok ? "text-green-500" : "text-slate-400 dark:text-slate-500"}`}>
                      {statusText}
                    </p>
                  </div>
                </div>
                <div className={`h-2 w-2 rounded-full ${ok ? "bg-green-500" : "bg-slate-300 dark:bg-slate-600"}`} />
              </div>
            ))}
      </CardContent>
    </Card>
  );
}

export default SystemStatus;
