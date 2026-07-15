/**
 * Design tokens — the single source for the visual language (UI plan §8).
 * Components reference these names (or the Tailwind classes they map to) so
 * status colors and channel colors stay consistent across every surface.
 *
 * These are TS constants (not CSS vars) so they're tree-shakeable and usable
 * in logic (e.g. picking a badge variant). Tailwind classes remain the primary
 * styling mechanism; this file documents the canonical mapping. Dark-mode
 * values are documented alongside light (every component pairs a light class
 * with a `dark:` sibling rather than switching to CSS variables).
 */

export const font = {
  sans: "Inter (next/font/google, var(--font-inter))",
} as const;

/** Extended indigo brand scale — see tailwind.config.ts `theme.extend.colors.primary`. */
export const primaryScale = {
  50: "#eef2ff",
  100: "#e0e7ff",
  200: "#c7d2fe",
  300: "#a5b4fc",
  400: "#818cf8",
  500: "#6366f1",
  600: "#4f46e5",
  700: "#4338ca",
  800: "#3730a3",
  900: "#312e81",
  950: "#1e1b4b",
} as const;

export const colors = {
  bg: { light: "#f1f5f9", dark: "#0b1120" }, // app background
  surface: { light: "#ffffff", dark: "#0f172a" }, // cards, tables
  sidebar: "#0f172a", // slate-900 — navigation (same in both themes, already dark)
  primary: primaryScale[600], // indigo-600 — actions, active nav
  text: { light: "#334155", dark: "#e2e8f0" }, // body
  muted: { light: "#64748b", dark: "#94a3b8" }, // secondary text
  border: { light: "#e2e8f0", dark: "#1e293b" },
  success: "#059669", // emerald-600
  warning: "#d97706", // amber-600
  danger: "#dc2626", // red-600
} as const;

export const radii = {
  card: "rounded-xl", // 12px
  container: "rounded-2xl", // 16px
} as const;

/** Elevation — see tailwind.config.ts `theme.extend.boxShadow`. Softer/more
 * layered than Tailwind's stock shadow-sm/md, used on Card and other raised
 * surfaces so the UI reads with real depth instead of flat borders alone. */
export const shadows = {
  card: "shadow-elevated",
  raised: "shadow-elevated-lg",
} as const;

/**
 * Status → visual language (UI plan §8.2). Domain badge components map their
 * semantic state to these Tailwind class bundles. Keeping the mapping here
 * means "live is amber, ended is slate" is defined once.
 */
export const statusStyles = {
  live: "bg-amber-100 text-amber-700 dark:bg-amber-500/15 dark:text-amber-400",
  ended: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
  success: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400",
  danger: "bg-red-100 text-red-700 dark:bg-red-500/15 dark:text-red-400",
} as const;

/** Channel → color (UI plan §8.2). Voice = indigo, WhatsApp = emerald. */
export const channelStyles = {
  voice: "bg-primary-100 text-primary-700 dark:bg-primary-500/15 dark:text-primary-400",
  whatsapp: "bg-emerald-100 text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400",
} as const;
