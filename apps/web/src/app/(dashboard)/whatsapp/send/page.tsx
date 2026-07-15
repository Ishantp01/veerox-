"use client";

import { PageHeader } from "@/components/layout/page-header";
import { OutboundWhatsAppForm } from "@/components/whatsapp/outbound-form";

export default function WhatsAppSendPage() {
  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Send Message"
        description="Send an outbound WhatsApp message — the recipient's reply resumes the AI agent conversation."
      />
      <OutboundWhatsAppForm />
    </div>
  );
}
