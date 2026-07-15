"use client";

import { LeadsView } from "@/components/leads/leads-view";

export default function WhatsAppLeadsPage() {
  return (
    <LeadsView
      title="Leads"
      description="Captured leads from WhatsApp conversations"
      channel="whatsapp"
      detailBasePath="/whatsapp/leads"
    />
  );
}
