"use client";

import { HumanSupportView } from "@/components/human-support/human-support-view";

export default function CallingHumanSupportPage() {
  return (
    <HumanSupportView
      title="Human Support"
      description="Live transfer_to_human events from calls — queue rows are pending pickup, lead rows are history."
      channel="voice"
      conversationBasePath="/calling/conversations"
    />
  );
}
