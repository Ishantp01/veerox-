"use client";

import { QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { ThemeProvider } from "next-themes";
import { useState } from "react";
import { makeQueryClient } from "@/lib/query";
import { ToastProvider } from "@/components/ui/toast";
import { AuthProvider } from "@/lib/auth-context";

/**
 * Client-side provider shell. Wraps the whole app so every page can use
 * TanStack Query hooks. The QueryClient is created once per browser session
 * via useState (not at module scope) so it isn't shared across SSR requests.
 *
 * Defaults to light on first visit; the topbar's theme toggle switches to
 * dark (`.dark` Tailwind variant, `tailwind.config.ts`'s `darkMode: "class"`)
 * — `enableSystem={false}` so that choice, not the OS preference, decides
 * the initial theme.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(makeQueryClient);

  return (
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false}>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <ToastProvider>{children}</ToastProvider>
        </AuthProvider>
        {process.env.NODE_ENV === "development" && <ReactQueryDevtools initialIsOpen={false} />}
      </QueryClientProvider>
    </ThemeProvider>
  );
}
