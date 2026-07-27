"use client";

import { LeadsView } from "@/components/leads/leads-view";

/**
 * Cross-channel CRM read view — same underlying rows as /calling/leads and
 * /whatsapp/leads, just not scoped to a single channel (LeadsView renders
 * all channels when `channel` is omitted).
 */
export default function CrmLeadsPage() {
  return (
    <LeadsView
      title="Leads"
      description="All leads captured across both AI Calling and AI WhatsApp"
      detailBasePath="/crm/leads"
    />
  );
}
