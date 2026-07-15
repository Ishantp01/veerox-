import { redirect } from "next/navigation";

// Settings are now split by channel under /whatsapp/settings and
// /calling/settings.
export default function SettingsRedirectPage() {
  redirect("/");
}
