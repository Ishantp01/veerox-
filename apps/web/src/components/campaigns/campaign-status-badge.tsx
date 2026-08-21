import { CircleDot, PauseCircle, CheckCircle2, FileEdit, Clock } from "lucide-react";
import { Badge } from "@/components/ui";
import type { CampaignStatus, CampaignTargetStatus } from "@/lib/types";

const CAMPAIGN_META: Record<CampaignStatus, { label: string; variant: "live" | "neutral" | "success" }> = {
  draft: { label: "Draft", variant: "neutral" },
  scheduled: { label: "Scheduled", variant: "neutral" },
  running: { label: "Running", variant: "live" },
  paused: { label: "Paused", variant: "neutral" },
  completed: { label: "Completed", variant: "success" },
};

const CAMPAIGN_ICON: Record<CampaignStatus, typeof CircleDot> = {
  draft: FileEdit,
  scheduled: Clock,
  running: CircleDot,
  paused: PauseCircle,
  completed: CheckCircle2,
};

export function CampaignStatusBadge({ status }: { status: CampaignStatus }) {
  const meta = CAMPAIGN_META[status] ?? CAMPAIGN_META.running;
  const icon = CAMPAIGN_ICON[status] ?? CircleDot;
  return (
    <Badge variant={meta.variant} icon={icon}>
      {meta.label}
    </Badge>
  );
}

const TARGET_META: Record<CampaignTargetStatus, { label: string; variant: "neutral" | "live" | "success" | "danger" }> = {
  pending: { label: "Pending", variant: "neutral" },
  calling: { label: "Calling", variant: "live" },
  completed: { label: "Completed", variant: "success" },
  failed: { label: "Failed", variant: "danger" },
};

export function CampaignTargetStatusBadge({ status }: { status: CampaignTargetStatus }) {
  const meta = TARGET_META[status] ?? TARGET_META.pending;
  return <Badge variant={meta.variant}>{meta.label}</Badge>;
}
