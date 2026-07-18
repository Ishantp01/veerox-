"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatShortDate } from "@/lib/format";
import type { ReportsTimeseriesPoint } from "@/lib/types";

// Categorical slots 1/2/3 (fixed order) from the dataviz palette — blue,
// green, magenta — stepped for each surface. Never reordered per-chart.
const COLORS = {
  light: { calls: "#2a78d6", whatsapp: "#008300", qualified: "#e87ba4" },
  dark: { calls: "#3987e5", whatsapp: "#008300", qualified: "#d55181" },
};
const CHROME = {
  light: { grid: "#e1e0d9", axis: "#898781", surface: "#fcfcfb" },
  dark: { grid: "#2c2c2a", axis: "#898781", surface: "#1a1a19" },
};

interface SeriesDef {
  key: keyof Pick<ReportsTimeseriesPoint, "calls" | "whatsapp_messages" | "qualified_count">;
  label: string;
  colorKey: keyof typeof COLORS.light;
}

const SERIES: SeriesDef[] = [
  { key: "calls", label: "Calls", colorKey: "calls" },
  { key: "whatsapp_messages", label: "WhatsApp Messages", colorKey: "whatsapp" },
  { key: "qualified_count", label: "Qualified Leads", colorKey: "qualified" },
];

function TrendTooltip({
  active,
  payload,
  label,
  colors,
}: {
  active?: boolean;
  payload?: { dataKey: string; value: number; color: string }[];
  label?: string;
  colors: typeof COLORS.light;
}) {
  if (!active || !payload?.length) return null;
  const byKey = Object.fromEntries(payload.map((p) => [p.dataKey, p]));
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-3.5 py-2.5 text-xs shadow-lg dark:border-slate-700 dark:bg-slate-900">
      <p className="mb-1.5 font-semibold text-slate-500 dark:text-slate-400">
        {formatShortDate(label)}
      </p>
      {SERIES.map(({ key, label: seriesLabel, colorKey }) => {
        const point = byKey[key];
        if (!point) return null;
        return (
          <p key={key} className="flex items-center gap-2 py-0.5">
            <span
              className="h-0.5 w-3 shrink-0 rounded-full"
              style={{ backgroundColor: colors[colorKey] }}
              aria-hidden
            />
            <span className="font-bold text-slate-800 dark:text-slate-100">{point.value}</span>
            <span className="text-slate-500 dark:text-slate-400">{seriesLabel}</span>
          </p>
        );
      })}
    </div>
  );
}

/**
 * Daily trend of calls / WhatsApp messages / qualified leads over the
 * selected window. Colors follow the dataviz skill's fixed categorical
 * order (slots 1/2/3 — blue/green/magenta), selected per light/dark surface
 * rather than an automatic CSS flip (next-themes' resolvedTheme).
 */
export function ReportsTrendChart({ data }: { data: ReportsTimeseriesPoint[] }) {
  const { resolvedTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const isDark = mounted && resolvedTheme === "dark";
  const colors = isDark ? COLORS.dark : COLORS.light;
  const chrome = isDark ? CHROME.dark : CHROME.light;

  return (
    <div className="h-72 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <CartesianGrid stroke={chrome.grid} vertical={false} />
          <XAxis
            dataKey="date"
            tickFormatter={formatShortDate}
            tick={{ fontSize: 11, fill: chrome.axis }}
            axisLine={{ stroke: chrome.grid }}
            tickLine={false}
          />
          <YAxis
            allowDecimals={false}
            tick={{ fontSize: 11, fill: chrome.axis }}
            axisLine={false}
            tickLine={false}
            width={40}
          />
          <Tooltip
            content={<TrendTooltip colors={colors} />}
            cursor={{ stroke: chrome.axis, strokeWidth: 1 }}
          />
          <Legend
            wrapperStyle={{ fontSize: 12, paddingTop: 8 }}
            formatter={(value) => <span className="text-slate-600 dark:text-slate-300">{value}</span>}
          />
          {SERIES.map(({ key, label, colorKey }) => (
            <Line
              key={key}
              type="monotone"
              dataKey={key}
              name={label}
              stroke={colors[colorKey]}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, strokeWidth: 2, stroke: chrome.surface }}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
