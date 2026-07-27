/**
 * Shared dataviz palette — categorical slots (fixed order, never cycled),
 * status colors, and chart chrome, stepped per light/dark surface per the
 * dataviz skill. Charts pick colors by array/object index (slot 0, 1, 2...)
 * rather than each redefining its own hex literals, so a chart added later
 * (sales dashboard, follow-ups activity) stays validated for free.
 */

export interface ChartColorSet {
  light: string;
  dark: string;
}

/** Categorical slots 1-8, fixed order — never reorder or cycle past slot 3 for all-pairs forms (scatter/bubble). */
export const CATEGORICAL: ChartColorSet[] = [
  { light: "#2a78d6", dark: "#3987e5" }, // 1 blue
  { light: "#eb6834", dark: "#d95926" }, // 2 orange
  { light: "#1baf7a", dark: "#199e70" }, // 3 aqua
  { light: "#eda100", dark: "#c98500" }, // 4 yellow
  { light: "#e87ba4", dark: "#d55181" }, // 5 magenta
  { light: "#008300", dark: "#008300" }, // 6 green
  { light: "#4a3aa7", dark: "#9085e9" }, // 7 violet
  { light: "#e34948", dark: "#e66767" }, // 8 red
];

export const STATUS = {
  good: { light: "#0ca30c", dark: "#0ca30c" },
  warning: { light: "#fab219", dark: "#fab219" },
  serious: { light: "#ec835a", dark: "#ec835a" },
  critical: { light: "#d03b3b", dark: "#d03b3b" },
} satisfies Record<string, ChartColorSet>;

export const CHART_CHROME = {
  light: { grid: "#e1e0d9", axis: "#898781", surface: "#fcfcfb" },
  dark: { grid: "#2c2c2a", axis: "#898781", surface: "#1a1a19" },
};

export function categoricalColor(slot: number, isDark: boolean): string {
  const set = CATEGORICAL[slot % CATEGORICAL.length];
  return isDark ? set.dark : set.light;
}
