"use client";

import Link from "next/link";
import {
  ArrowRight,
  Megaphone,
  MessageSquareText,
  Phone,
  UserCheck,
  AlertTriangle,
  type LucideIcon,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui";

interface Action {
  href: string;
  label: string;
  description: string;
  icon: LucideIcon;
}

const VOICE_ACTIONS: Action[] = [
  { href: "/calling/dial", label: "Dial a number", description: "Place a one-off outbound call", icon: Phone },
  { href: "/calling/campaigns", label: "New campaign", description: "Bulk-call and qualify a lead list", icon: Megaphone },
  { href: "/calling/leads", label: "View leads", description: "See who the agent has captured", icon: UserCheck },
  { href: "/calling/escalations", label: "Escalations", description: "Conversations needing a human", icon: AlertTriangle },
];

const WHATSAPP_ACTIONS: Action[] = [
  { href: "/whatsapp/send", label: "Send a message", description: "One-off outbound WhatsApp text", icon: MessageSquareText },
  { href: "/whatsapp/leads", label: "View leads", description: "See who the agent has captured", icon: UserCheck },
  { href: "/whatsapp/escalations", label: "Escalations", description: "Conversations needing a human", icon: AlertTriangle },
];

const ALL_ACTIONS: Action[] = [
  { href: "/calling/dial", label: "Dial a number", description: "Place a one-off outbound call", icon: Phone },
  { href: "/calling/campaigns", label: "New campaign", description: "Bulk-call and qualify a lead list", icon: Megaphone },
  { href: "/whatsapp/send", label: "Send a WhatsApp message", description: "One-off outbound text", icon: MessageSquareText },
];

const ACTIONS_BY_VARIANT: Record<"all" | "whatsapp" | "voice", Action[]> = {
  all: ALL_ACTIONS,
  whatsapp: WHATSAPP_ACTIONS,
  voice: VOICE_ACTIONS,
};

export interface QuickActionsProps {
  variant: "all" | "whatsapp" | "voice";
}

/** Sidecar panel next to the dashboard's trend chart — shortcuts to the
 * most common next steps, so the page reads as a working tool rather than
 * a bare metrics readout. */
export function QuickActions({ variant }: QuickActionsProps) {
  const actions = ACTIONS_BY_VARIANT[variant];

  return (
    <Card>
      <CardHeader>
        <CardTitle>Quick actions</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-1.5 p-3">
        {actions.map(({ href, label, description, icon: Icon }) => (
          <Link
            key={href}
            href={href}
            className="group flex items-center gap-3 rounded-lg px-3 py-3 transition-colors hover:bg-slate-50"
          >
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary-50 text-primary-600">
              <Icon size={17} aria-hidden />
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold text-slate-800">{label}</p>
              <p className="truncate text-xs text-slate-400">{description}</p>
            </div>
            <ArrowRight
              size={15}
              className="shrink-0 text-slate-300 transition-transform group-hover:translate-x-0.5 group-hover:text-primary-500"
              aria-hidden
            />
          </Link>
        ))}
      </CardContent>
    </Card>
  );
}
