"use client";

import type { ReactNode } from "react";
import { LayoutDashboard, MessageSquare, UserCheck, AlertTriangle, Send, Settings } from "lucide-react";
import { SectionTabs } from "@/components/layout/section-tabs";

const ITEMS = [
  { href: "/whatsapp", label: "Dashboard", Icon: LayoutDashboard },
  { href: "/whatsapp/conversations", label: "Conversations", Icon: MessageSquare },
  { href: "/whatsapp/leads", label: "Leads", Icon: UserCheck },
  { href: "/whatsapp/escalations", label: "Escalations", Icon: AlertTriangle },
  { href: "/whatsapp/send", label: "Send Message", Icon: Send },
  { href: "/whatsapp/settings", label: "Settings", Icon: Settings },
];

export default function WhatsappLayout({ children }: { children: ReactNode }) {
  return (
    <div>
      <SectionTabs items={ITEMS} sectionRoot="/whatsapp" />
      {children}
    </div>
  );
}
