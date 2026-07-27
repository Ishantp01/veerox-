"use client";

import type { ReactNode } from "react";
import { LayoutDashboard, MessageSquare, UserCheck, AlertTriangle, Phone, Settings } from "lucide-react";
import { SectionTabs } from "@/components/layout/section-tabs";

const ITEMS = [
  { href: "/calling", label: "Dashboard", Icon: LayoutDashboard },
  { href: "/calling/conversations", label: "Conversations", Icon: MessageSquare },
  { href: "/calling/leads", label: "Leads", Icon: UserCheck },
  { href: "/calling/escalations", label: "Escalations", Icon: AlertTriangle },
  { href: "/calling/dial", label: "Dial", Icon: Phone },
  { href: "/calling/settings", label: "Settings", Icon: Settings },
];

export default function CallingLayout({ children }: { children: ReactNode }) {
  return (
    <div>
      <SectionTabs items={ITEMS} sectionRoot="/calling" />
      {children}
    </div>
  );
}
