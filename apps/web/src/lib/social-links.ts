import { FaFacebook, FaGlobe, FaInstagram, FaLinkedin, FaWhatsapp, FaXTwitter, FaYoutube } from "react-icons/fa6";
import type { IconType } from "react-icons";

// Fixed key -> icon/label mapping matching PlatformSettingsPanel's
// SOCIAL_FIELDS (apps/web/src/components/billing/platform-settings-panel.tsx)
// — shared by the topbar icon row so it stays in sync with what the platform
// admin configures. Any key outside this set is silently ignored rather than
// crashing the UI. Real brand marks via react-icons (lucide-react dropped
// brand-specific icons).
export const SOCIAL_META: Record<string, { label: string; Icon: IconType }> = {
  twitter: { label: "Twitter / X", Icon: FaXTwitter },
  linkedin: { label: "LinkedIn", Icon: FaLinkedin },
  instagram: { label: "Instagram", Icon: FaInstagram },
  facebook: { label: "Facebook", Icon: FaFacebook },
  youtube: { label: "YouTube", Icon: FaYoutube },
  whatsapp: { label: "WhatsApp", Icon: FaWhatsapp },
  website: { label: "Website", Icon: FaGlobe },
};
