import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Muted indigo/violet brand scale (closer to a Linear-style accent
        // than stock Tailwind indigo) — less saturated so it reads as a
        // precise accent color rather than a loud "purple app" statement.
        primary: {
          50: "#f5f6fe",
          100: "#ebedfd",
          200: "#d4d8fa",
          300: "#b0b8f5",
          400: "#838fed",
          500: "#5e6ad2",
          600: "#4c56c4",
          700: "#3f47a3",
          800: "#363c85",
          900: "#2f346d",
          950: "#1d1f42",
        },
        // Flat, slightly-cool neutral scale used for the sidebar and other
        // near-black surfaces — plain gray rather than slate's blue tint,
        // for a calmer "console" feel.
        canvas: {
          50: "#fafafa",
          100: "#f4f4f5",
          900: "#111113",
          950: "#0a0a0b",
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
        glow: "0 8px 24px -8px rgb(94 106 210 / 0.45)",
        "glow-lg": "0 16px 40px -12px rgb(94 106 210 / 0.5)",
      },
      backgroundImage: {
        "mesh-light":
          "radial-gradient(at 20% 0%, rgb(94 106 210 / 0.10) 0px, transparent 50%), radial-gradient(at 90% 10%, rgb(56 189 248 / 0.10) 0px, transparent 45%), radial-gradient(at 90% 90%, rgb(16 185 129 / 0.06) 0px, transparent 50%)",
        "mesh-dark":
          "radial-gradient(at 20% 0%, rgb(94 106 210 / 0.18) 0px, transparent 50%), radial-gradient(at 90% 10%, rgb(56 189 248 / 0.10) 0px, transparent 45%), radial-gradient(at 90% 90%, rgb(16 185 129 / 0.08) 0px, transparent 50%)",
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
