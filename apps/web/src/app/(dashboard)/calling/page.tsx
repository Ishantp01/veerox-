"use client";

import { PageHeader } from "@/components/layout/page-header";
import { StatsGrid } from "@/components/dashboard/stats-grid";

export default function CallingDashboardPage() {
  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="AI Calling Agent"
        description="Real-time overview of your voice calling channel"
      />
      <StatsGrid variant="voice" />
    </div>
  );
}
