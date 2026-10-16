"use client";

import { useState } from "react";

import { QueryBoundary } from "@/components/layout/query-boundary";
import { Card, CardContent, Label, Select } from "@/components/ui";
import { useEngineMetrics } from "@/lib/hooks";

function pct(value: number | null): string {
  return value === null ? "—" : `${Math.round(value * 1000) / 10}%`;
}

function ms(value: number | null): string {
  return value === null ? "—" : `${value} ms`;
}

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-slate-200 p-3 dark:border-slate-700">
      <p className="text-xs text-slate-500 dark:text-slate-400">{label}</p>
      <p className="mt-1 text-xl font-semibold text-slate-800 dark:text-slate-100">{value}</p>
      {hint && <p className="mt-0.5 text-xs text-slate-400">{hint}</p>}
    </div>
  );
}

export function MetricsPanel() {
  const [days, setDays] = useState("7");
  const [channel, setChannel] = useState<"" | "whatsapp" | "voice">("");
  const metrics = useEngineMetrics({ days: Number(days), channel: channel || undefined });
  const data = metrics.data;
  const scenarios = Object.entries(data?.scenarios ?? {});
  const reasons = Object.entries(data?.fallback_reasons ?? {});
  const maxScenario = Math.max(1, ...scenarios.map(([, n]) => n));

  return (
    <Card>
      <CardContent className="flex flex-col gap-4 p-4">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold text-slate-800 dark:text-slate-100">Rollout metrics</h2>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              Measured from the turn log. Use them to decide whether to enable more organizations or
              widen voice.
            </p>
          </div>
          <div className="flex items-end gap-3">
            <div>
              <Label htmlFor="metrics-days">Period</Label>
              <Select id="metrics-days" value={days} onChange={setDays}>
                <option value="1">Last 24 hours</option>
                <option value="7">Last 7 days</option>
                <option value="30">Last 30 days</option>
                <option value="90">Last 90 days</option>
              </Select>
            </div>
            <div>
              <Label htmlFor="metrics-channel">Channel</Label>
              <Select
                id="metrics-channel"
                value={channel}
                onChange={(v) => setChannel(v as "" | "whatsapp" | "voice")}
              >
                <option value="">All channels</option>
                <option value="whatsapp">WhatsApp</option>
                <option value="voice">Voice</option>
              </Select>
            </div>
          </div>
        </div>

        <QueryBoundary
          isLoading={metrics.isLoading}
          isError={metrics.isError}
          error={metrics.error}
          isEmpty={false}
          onRetry={() => metrics.refetch()}
        >
          {data && (
            <>
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
                <Stat label="Turns handled" value={String(data.turns)} hint={`${data.conversations} conversations`} />
                <Stat
                  label="Scenario accuracy"
                  value={pct(data.scenario_accuracy)}
                  hint={`${data.reviewed_turns} turns reviewed by a person`}
                />
                <Stat
                  label="Fallback rate"
                  value={pct(data.fallback_rate)}
                  hint={`${data.fallbacks} handed back to the script`}
                />
                <Stat
                  label="Reply time (p95)"
                  value={ms(data.latency_ms.p95)}
                  hint={`median ${ms(data.latency_ms.p50)} · mean ${ms(data.latency_ms.mean)}`}
                />
                <Stat label="Call completion" value={pct(data.call_completion_rate)} hint="reached a finished state" />
                <Stat
                  label="Appointment conversion"
                  value={pct(data.appointment_conversion)}
                  hint={`${data.appointments_booked} booked`}
                />
                <Stat label="Transfer rate" value={pct(data.transfer_rate)} hint={`${data.transfers} handed to a person`} />
                <Stat
                  label="Left to the script"
                  value={String(data.handed_to_script_by_design)}
                  hint="Smart Script turns, by design"
                />
              </div>

              {data.reviewed_turns === 0 && (
                <p className="text-xs text-slate-500">
                  Scenario accuracy appears once someone marks turns Right or Wrong in the Overview
                  tab&apos;s turn log. Review a sample of shadow-mode voice turns before going live.
                </p>
              )}

              <div className="grid gap-4 lg:grid-cols-2">
                <div>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Scenarios seen
                  </h3>
                  {scenarios.length === 0 ? (
                    <p className="text-sm text-slate-400">No turns in this period.</p>
                  ) : (
                    <ul className="flex flex-col gap-1.5">
                      {scenarios.map(([name, count]) => (
                        <li key={name} className="flex items-center gap-2 text-sm">
                          <span className="w-44 shrink-0 truncate">{name}</span>
                          <span
                            className="h-2 rounded-full bg-primary-500"
                            style={{ width: `${Math.max(4, (count / maxScenario) * 100)}%`, maxWidth: "60%" }}
                            aria-hidden
                          />
                          <span className="text-xs text-slate-500">{count}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <div>
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Why it fell back
                  </h3>
                  {reasons.length === 0 ? (
                    <p className="text-sm text-slate-400">No fallbacks in this period.</p>
                  ) : (
                    <ul className="flex flex-col gap-1 text-sm">
                      {reasons.map(([reason, count]) => (
                        <li key={reason} className="flex justify-between">
                          <span>{reason}</span>
                          <span className="text-slate-500">{count}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              </div>
            </>
          )}
        </QueryBoundary>
      </CardContent>
    </Card>
  );
}
