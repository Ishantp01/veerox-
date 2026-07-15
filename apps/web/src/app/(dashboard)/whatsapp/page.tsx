"use client";

import { PageHeader } from "@/components/layout/page-header";
import { StatsGrid } from "@/components/dashboard/stats-grid";

export default function WhatsAppDashboardPage() {
  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="WhatsApp AI Agent"
        description="Real-time overview of your WhatsApp channel"
      />
      <StatsGrid variant="whatsapp" />
    </div>
  );
}
