"use client";

import { EscalationsView } from "@/components/escalations/escalations-view";

export default function CallingEscalationsPage() {
  return (
    <EscalationsView
      title="Escalations"
      description="Live transfer_to_human events from calls — queue rows are pending pickup, lead rows are history."
      channel="voice"
      conversationBasePath="/calling/conversations"
    />
  );
}
