import { redirect } from "next/navigation";

// Campaigns moved to /automation/campaigns and are now channel-aware
// (voice + WhatsApp) rather than calling-only.
export default function CallingCampaignsRedirectPage() {
  redirect("/automation/campaigns");
}
