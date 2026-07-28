import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Blue brand scale — "Lumina Dark" accent (#2f81f7), matches the
        // VeeROX mockup's sidebar active state, primary buttons, links, and
        // chart line.
        primary: {
          50: "#eff6ff",
          100: "#dbeafe",
          200: "#bfdbfe",
          300: "#93c5fd",
          400: "#60a5fa",
          500: "#2f81f7",
          600: "#0052cc",
          700: "#0043a6",
          800: "#003480",
          900: "#00265c",
          950: "#001838",
        },
        // Flat, slightly-cool neutral scale used for the sidebar and other
        // near-black surfaces — plain gray rather than slate's blue tint,
        // for a calmer "console" feel.
        canvas: {
          50: "#fafafa",
          100: "#f4f4f5",
          200: "#e4e4e7",
          300: "#d4d4d8",
          400: "#a1a1aa",
          500: "#71717a",
          600: "#52525b",
          700: "#3f3f46",
          800: "#201f22",
          900: "#0a0d14",
          950: "#0a0d14",
        },
        // Override Tailwind's stock `slate` so every existing `dark:bg-slate-900`
        // / `dark:border-slate-800` / `dark:text-slate-400` class across the
        // app (Card, Table, Button, badges, dialogs, …) picks up the VeeROX
        // "Lumina Dark" surface palette (bg #0a0d14, sidebar #0d1117, card
        // #161b22, border #30363d) without touching every component file.
        // Light-mode shades (50-300) are left close to stock.
        slate: {
          50: "#f8fafc",
          100: "#f1f5f9",
          200: "#e2e8f0",
          300: "#cbd5e1",
          400: "#8b93a7",
          500: "#5b6478",
          600: "#4b5468",
          700: "#30363d",
          800: "#161b22",
          900: "#0a0d14",
          950: "#0d1117",
        },
      },
      fontFamily: {
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      boxShadow: {
        // Hairline elevation — a single soft, low-opacity layer instead of
        // stacked colored shadows, so surfaces read as flat + precise with
        // depth coming mostly from borders.
        elevated: "0 1px 2px 0 rgb(0 0 0 / 0.04)",
        "elevated-lg": "0 4px 16px -4px rgb(0 0 0 / 0.10)",
        // Premium tiers: a soft ambient card shadow and a colored "glow" used
        // sparingly on primary actions / active nav states for the rich-SaaS
        // direction — depth + a touch of brand color instead of pure gray.
        card: "0 1px 2px 0 rgb(15 23 42 / 0.04), 0 12px 24px -12px rgb(15 23 42 / 0.10)",
        "card-lg": "0 2px 4px 0 rgb(15 23 42 / 0.04), 0 24px 48px -16px rgb(15 23 42 / 0.16)",
        glow: "0 8px 24px -8px rgb(47 129 247 / 0.45)",
        "glow-lg": "0 16px 40px -12px rgb(47 129 247 / 0.5)",
      },
      backgroundImage: {
        "mesh-light":
          "radial-gradient(at 20% 0%, rgb(47 129 247 / 0.10) 0px, transparent 50%), radial-gradient(at 90% 10%, rgb(56 189 248 / 0.10) 0px, transparent 45%), radial-gradient(at 90% 90%, rgb(16 185 129 / 0.06) 0px, transparent 50%)",
        "mesh-dark":
          "radial-gradient(at 20% 0%, rgb(47 129 247 / 0.18) 0px, transparent 50%), radial-gradient(at 90% 10%, rgb(56 189 248 / 0.10) 0px, transparent 45%), radial-gradient(at 90% 90%, rgb(16 185 129 / 0.08) 0px, transparent 50%)",
        "sidebar-fade": "linear-gradient(180deg, rgb(255 255 255 / 0.06), transparent 20%)",
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
