"use client";

import { EscalationsView } from "@/components/escalations/escalations-view";

export default function EscalationsPage() {
  return (
    <EscalationsView
      title="Escalations"
      description="Live transfer_to_human events across calls and WhatsApp — queue rows are pending pickup, lead rows are history."
      conversationBasePath="/conversations"
    />
  );
}
