"use client";

import { useMemo } from "react";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { Card, CardContent, CardHeader, CardTitle, Skeleton } from "@/components/ui";
import { useReportsTimeseries } from "@/lib/hooks";
import type { ReportsTimeseriesPoint } from "@/lib/types";

const TREND_DAYS = 14;

interface Series {
  key: keyof ReportsTimeseriesPoint;
  label: string;
  color: string;
}

const SERIES_BY_VARIANT: Record<"all" | "whatsapp" | "voice", Series[]> = {
  voice: [{ key: "calls", label: "Calls", color: "#4f46e5" }],
  whatsapp: [{ key: "whatsapp_messages", label: "Messages", color: "#059669" }],
  all: [
    { key: "calls", label: "Calls", color: "#4f46e5" },
    { key: "whatsapp_messages", label: "WhatsApp messages", color: "#059669" },
  ],
};

function formatDayLabel(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

interface TooltipPayloadEntry {
  dataKey: string;
  value: number;
  color: string;
  name: string;
}

function ChartTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: TooltipPayloadEntry[];
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-3 py-2 text-xs shadow-card-lg dark:border-slate-700 dark:bg-slate-900">
      <p className="mb-1 font-semibold text-slate-700 dark:text-slate-200">
        {label && formatDayLabel(label)}
      </p>
      {payload.map((entry) => (
        <p key={entry.dataKey} className="flex items-center gap-1.5 text-slate-500 dark:text-slate-400">
          <span
            className="h-1.5 w-1.5 rounded-full"
            style={{ backgroundColor: entry.color }}
            aria-hidden
          />
          {entry.name}: <span className="font-semibold text-slate-700 dark:text-slate-200">{entry.value}</span>
        </p>
      ))}
    </div>
  );
}

export interface StatsTrendChartProps {
  variant: "all" | "whatsapp" | "voice";
}

/**
 * Trend line for the dashboard — the "beyond stat cards" data visualization
 * the old design lacked entirely. Backed by the existing GET
 * /admin/reports/timeseries endpoint (built for the reports page) rather
 * than a new one, since the data shape already matches.
 */
export function StatsTrendChart({ variant }: StatsTrendChartProps) {
  const { data, isLoading, isError } = useReportsTimeseries(TREND_DAYS);
  const series = SERIES_BY_VARIANT[variant];

  const hasData = useMemo(() => (data?.length ?? 0) > 0, [data]);

  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle>Activity — last {TREND_DAYS} days</CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-56 w-full" />
        ) : isError || !hasData ? (
          <p className="py-16 text-center text-sm text-slate-400 dark:text-slate-500">
            Not enough activity yet to show a trend.
          </p>
        ) : (
          <div className="h-56 w-full">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={data} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
                <defs>
                  {series.map((s) => (
                    <linearGradient key={s.key} id={`trend-${s.key}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={s.color} stopOpacity={0.35} />
                      <stop offset="100%" stopColor={s.color} stopOpacity={0} />
                    </linearGradient>
                  ))}
                </defs>
                <CartesianGrid strokeDasharray="3 3" vertical={false} className="stroke-slate-100 dark:stroke-slate-800" />
                <XAxis
                  dataKey="date"
                  tickFormatter={formatDayLabel}
                  tick={{ fontSize: 11, fill: "currentColor" }}
                  className="text-slate-400 dark:text-slate-500"
                  axisLine={false}
                  tickLine={false}
                  minTickGap={24}
                />
                <YAxis
                  tick={{ fontSize: 11, fill: "currentColor" }}
                  className="text-slate-400 dark:text-slate-500"
                  axisLine={false}
                  tickLine={false}
                  width={40}
                  allowDecimals={false}
                  // Headroom above the max value (and a floor below 0) so a
                  // flat all-zero series still renders as a visible line with
                  // dots instead of collapsing invisibly onto the x-axis.
                  domain={[-0.5, (max: number) => Math.max(max, 4)]}
                />
                <Tooltip content={<ChartTooltip />} />
                {series.map((s) => (
                  <Area
                    key={s.key}
                    type="monotone"
                    dataKey={s.key}
                    name={s.label}
                    stroke={s.color}
                    strokeWidth={2.5}
                    fill={`url(#trend-${s.key})`}
                    dot={{ r: 3, strokeWidth: 0, fill: s.color }}
                    activeDot={{ r: 5, strokeWidth: 2, stroke: "#fff" }}
                  />
                ))}
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
