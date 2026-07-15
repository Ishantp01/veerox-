"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { ThemeProvider } from "next-themes";
import { useState } from "react";
import { makeQueryClient } from "@/lib/query";
import { ToastProvider } from "@/components/ui/toast";

/**
 * Client-side provider shell. Wraps the whole app so every page can use
 * TanStack Query hooks. The QueryClient is created once per browser session
 * via useState (not at module scope) so it isn't shared across SSR requests.
 *
 * Light mode only, by design — `forcedTheme="light"` keeps the `.dark`
 * Tailwind variant (tailwind.config.ts's `darkMode: "class"`) permanently
 * inert regardless of OS preference, so the product presents one polished
 * theme instead of a half-supported toggle.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(makeQueryClient);

  return (
    <ThemeProvider attribute="class" forcedTheme="light">
      <QueryClientProvider client={queryClient}>
        <ToastProvider>{children}</ToastProvider>
        {process.env.NODE_ENV === "development" && <ReactQueryDevtools initialIsOpen={false} />}
      </QueryClientProvider>
    </ThemeProvider>
  );
}
