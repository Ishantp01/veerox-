"use client";

import { HumanSupportView } from "@/components/human-support/human-support-view";

export default function HumanSupportPage() {
  return (
    <HumanSupportView
      title="Human Support"
      description="Live transfer_to_human events across calls and WhatsApp — queue rows are pending pickup, lead rows are history."
      conversationBasePath="/conversations"
    />
  );
}
