"use client";

import { PageHeader } from "@/components/layout/page-header";
import { OutboundWhatsAppForm } from "@/components/whatsapp/outbound-form";
import { WhatsAppSendAnalyticsPanel } from "@/components/whatsapp/send-analytics-panel";

export default function WhatsAppSendPage() {
  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="WhatsApp AI Agent"
        description="Send an outbound WhatsApp message — the recipient's reply resumes the AI agent conversation."
      />
      <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
        <OutboundWhatsAppForm />
        <WhatsAppSendAnalyticsPanel />
      </div>
    </div>
  );
}
