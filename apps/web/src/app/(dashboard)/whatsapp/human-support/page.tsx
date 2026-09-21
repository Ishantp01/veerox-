"use client";

import { HumanSupportView } from "@/components/human-support/human-support-view";

export default function WhatsAppHumanSupportPage() {
  return (
    <HumanSupportView
      title="Human Support"
      description="Live transfer_to_human events from WhatsApp — queue rows are pending pickup, lead rows are history."
      channel="whatsapp"
      conversationBasePath="/whatsapp/conversations"
    />
  );
}
