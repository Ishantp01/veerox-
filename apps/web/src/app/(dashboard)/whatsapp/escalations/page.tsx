"use client";

import { EscalationsView } from "@/components/escalations/escalations-view";

export default function WhatsAppEscalationsPage() {
  return (
    <EscalationsView
      title="Escalations"
      description="Live transfer_to_human events from WhatsApp — queue rows are pending pickup, lead rows are history."
      channel="whatsapp"
      conversationBasePath="/whatsapp/conversations"
    />
  );
}
