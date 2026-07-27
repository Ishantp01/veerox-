"use client";

import { useParams } from "next/navigation";
import { CampaignDetail } from "@/components/campaigns/campaign-detail";

export default function CampaignDetailPage() {
  const params = useParams();
  const id = typeof params.id === "string" ? params.id : (params.id?.[0] ?? "");

  return <CampaignDetail campaignId={id} />;
}
