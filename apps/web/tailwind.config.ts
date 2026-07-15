import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: "class",
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        // Extended indigo-based brand scale — replaces the previously-unused
        // grayscale "primary" ramp. Tuned so 600/700 (primary actions, active
        // nav) carry more depth than Tailwind's stock indigo at those steps.
        primary: {
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
        },
      },
      fontFamily: {
        sans: ["var(--font-inter)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      boxShadow: {
        // Softer, more layered than Tailwind's stock shadow-sm/md — used on
        // Card and other elevated surfaces for a less "flat" default look.
        elevated: "0 1px 2px 0 rgb(15 23 42 / 0.04), 0 4px 16px -4px rgb(15 23 42 / 0.08)",
        "elevated-lg": "0 2px 4px 0 rgb(15 23 42 / 0.04), 0 12px 32px -8px rgb(15 23 42 / 0.12)",
      },
    },
  },
  plugins: [],
};

export default config;
