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
      },
    },
  },
  plugins: [],
};

export default config;
