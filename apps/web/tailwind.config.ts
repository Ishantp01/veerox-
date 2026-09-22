import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: [
    "./src/**/*.{ts,tsx}",
    // Onborda ships its own utility classes in the compiled bundle — Tailwind
    // must scan it or the tour overlay/card render unstyled.
    "./node_modules/onborda/dist/**/*.{js,mjs}",
  ],
  theme: {
    extend: {
      colors: {
        // Blue accent — matches the Veerox console mockup's --gold token
        // (#4C82F0), used for sidebar active state, primary buttons, links,
        // and chart line.
        // 500 is the mockup's dark-theme --gold (#4C82F0); 600 is its
        // light-theme --gold (#265CC9) — more saturated so text/fills stay
        // readable on a white surface. The rest of the app already follows
        // this "600 for light, 400/500 for dark" convention (badges, links,
        // Select's highlighted row, …), so most components pick up the exact
        // mockup accent per theme automatically.
        primary: {
          50: "#eff5ff",
          100: "#dbe8fe",
          200: "#bdd6fd",
          300: "#8fbafb",
          400: "#6a9ef7",
          500: "#4C82F0",
          600: "#265CC9",
          700: "#1e49a1",
          800: "#1c3d84",
          900: "#1a326a",
          950: "#122043",
        },
        // Flat, slightly-warm neutral scale used for the page canvas and
        // other near-black surfaces — matches the mockup's --bg exactly in
        // both themes (#F6F6F4 light / #0A0C0E dark).
        canvas: {
          50: "#F6F6F4",
          100: "#EFEEEA",
          200: "#e4e4e7",
          300: "#d4d4d8",
          400: "#a1a1aa",
          500: "#71717a",
          600: "#52525b",
          700: "#3f3f46",
          800: "#121519",
          900: "#0A0C0E",
          950: "#0A0C0E",
        },
        // Override Tailwind's stock `slate` so every existing `bg-slate-*`
        // / `border-slate-*` / `text-slate-*` class across the app (Card,
        // Table, Button, badges, dialogs, …) picks up the Veerox console
        // mockup's surface palette in BOTH themes without touching every
        // component file: light = surface #FFFFFF (native white, unchanged),
        // surface-2 #FBFAF8, surface-3 #F0EFEC, border #E4E2DD, text-mid
        // #5C6570, text-low #8B9198; dark = surface #121519, surface-2
        // #191D22, surface-3 #20252B, border #262B31, bg #0A0C0E.
        slate: {
          50: "#FBFAF8",
          100: "#F0EFEC",
          200: "#E4E2DD",
          300: "#D6D3CC",
          400: "#8B9198",
          500: "#5C6570",
          600: "#495059",
          700: "#262B31",
          800: "#191D22",
          900: "#121519",
          950: "#0A0C0E",
        },
        // Override stock `emerald`/`red` so success/whatsapp badges and
        // danger buttons pick up the mockup's --teal (#35C7B0) and --danger
        // (#E5584F) accents everywhere those Tailwind classes are already
        // used, the same trick as the `slate` override above.
        // 400/500 (dark-mode wash + text) = the mockup's dark-theme --teal
        // (#35C7B0); 700 (light-mode text, e.g. badge/link success color) =
        // its light-theme --teal (#128F7C).
        emerald: {
          50: "#e9faf6",
          100: "#cdf3ea",
          200: "#9de7d6",
          300: "#65d5bd",
          400: "#35C7B0",
          500: "#35C7B0",
          600: "#189981",
          700: "#128F7C",
          800: "#0f7565",
          900: "#0d5d51",
          950: "#0a2b26",
        },
        // Same pattern for danger: 400/500 = dark-theme --danger (#E5584F);
        // 700 (light-mode text) = light-theme --danger (#C4443C).
        red: {
          50: "#fdeeed",
          100: "#fbd9d6",
          200: "#f7b8b3",
          300: "#f0918a",
          400: "#e97169",
          500: "#E5584F",
          600: "#d14d43",
          700: "#C4443C",
          800: "#a13830",
          900: "#7d2c26",
          950: "#3a1512",
        },
      },
      fontFamily: {
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      boxShadow: {
        // Hairline elevation — a single soft, low-opacity layer instead of
        // stacked colored shadows, so surfaces read as flat + precise with
        // depth coming mostly from borders, matching the mockup's flat
        // "console" direction rather than a colored glow.
        elevated: "0 1px 2px 0 rgb(0 0 0 / 0.04)",
        "elevated-lg": "0 4px 16px -4px rgb(0 0 0 / 0.10)",
        card: "0 1px 2px rgba(0,0,0,.4)",
        "card-lg": "0 8px 24px rgba(0,0,0,.45)",
        glow: "0 1px 2px rgba(0,0,0,.4)",
        "glow-lg": "0 8px 24px rgba(0,0,0,.45)",
      },
      backgroundImage: {
        // Flat backgrounds only — the mockup's canvas has no colored mesh
        // wash, so these resolve to nothing (kept as no-ops so any remaining
        // `bg-mesh-*`/`bg-sidebar-fade` class reference doesn't error).
        "mesh-light": "none",
        "mesh-dark": "none",
        "sidebar-fade": "none",
      },
      keyframes: {
        "fade-up": {
          "0%": { opacity: "0", transform: "translateY(6px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-up": "fade-up 0.35s ease-out both",
      },
    },
  },
  plugins: [],
};

export default config;
