import { redirect } from "next/navigation";

// Campaigns moved to /automation/campaigns and are now channel-aware
// (voice + WhatsApp) rather than calling-only.
export default function CallingCampaignDetailRedirectPage({
  params,
}: {
  params: { id: string };
}) {
  redirect(`/automation/campaigns/${params.id}`);
}
