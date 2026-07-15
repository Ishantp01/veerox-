"use client";

import { LeadsView } from "@/components/leads/leads-view";

export default function CallingLeadsPage() {
  return (
    <LeadsView
      title="Leads"
      description="Captured leads from voice calls"
      channel="voice"
      detailBasePath="/calling/leads"
    />
  );
}
