import type { Metadata } from "next";
import { Inter } from "next/font/google";
import "./globals.css";
import { Providers } from "@/components/providers";

const inter = Inter({ subsets: ["latin"], variable: "--font-inter" });

export const metadata: Metadata = {
  title: "Veerox AI — Admin",
  description: "Admin dashboard for Veerox AI voice + WhatsApp agent",
};

/**
 * Root layout: html/body + global providers only. The sidebar shell lives in
 * the (dashboard) route group so the (auth) login page can opt out of it.
 *
 * `suppressHydrationWarning` on <html> is required by next-themes — it sets
 * the "dark"/"light" class client-side before paint, which necessarily
 * differs from the server-rendered markup for a split second.
 */
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" suppressHydrationWarning className={inter.variable}>
      <body className="bg-gradient-to-b from-slate-50 to-slate-100 text-slate-700 antialiased font-sans dark:bg-[#0b1120] dark:text-slate-200">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
