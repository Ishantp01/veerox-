"use client";

import { useEffect } from "react";
import { useTheme } from "next-themes";

/**
 * Auth screens are always light, whatever theme the dashboard is set to.
 * Strips the `.dark` class from <html> while mounted (re-stripping if
 * next-themes re-applies it) and restores it for the stored theme on leave.
 * The stored theme itself is never changed.
 */
export function ForceLight() {
  const { resolvedTheme } = useTheme();

  useEffect(() => {
    const root = document.documentElement;
    const strip = () => {
      if (root.classList.contains("dark")) root.classList.remove("dark");
      if (root.style.colorScheme !== "light") root.style.colorScheme = "light";
    };
    strip();
    const obs = new MutationObserver(strip);
    obs.observe(root, { attributes: true, attributeFilter: ["class", "style"] });
    return () => {
      obs.disconnect();
      if (resolvedTheme === "dark") root.classList.add("dark");
      root.style.colorScheme = resolvedTheme === "dark" ? "dark" : "light";
    };
  }, [resolvedTheme]);

  return null;
}
