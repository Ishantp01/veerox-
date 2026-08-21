import { Badge } from "@/components/ui/badge";

export interface ChannelBadgeProps {
  /** Not tied to Conversation specifically — anything with a plain binary
   * voice/whatsapp channel (Conversation, Lead, CampaignTarget, ...) can use
   * this badge. */
  channel: "voice" | "whatsapp";
}

/**
 * Channel indicator (UI plan §8.2): voice = indigo + mic, WhatsApp = emerald +
 * message. Color is never the only signal — the Badge primitive pairs each
 * variant with an icon and we render the channel name as text.
 */
export function ChannelBadge({ channel }: ChannelBadgeProps) {
  if (channel === "voice") {
    return <Badge variant="voice">Voice</Badge>;
  }
  return <Badge variant="whatsapp">WhatsApp</Badge>;
}

export default ChannelBadge;

export interface CampaignChannelBadgeProps {
  /** A Campaign's channel is a display summary that can also be "mixed"
   * when it holds both voice and WhatsApp targets — see Campaign.channel. */
  channel: "voice" | "whatsapp" | "mixed";
}

/** Same visual language as ChannelBadge, plus a neutral variant for
 * campaigns whose targets span both channels. */
export function CampaignChannelBadge({ channel }: CampaignChannelBadgeProps) {
  if (channel === "mixed") {
    return <Badge variant="neutral">WhatsApp/Voice</Badge>;
  }
  return <ChannelBadge channel={channel} />;
}
