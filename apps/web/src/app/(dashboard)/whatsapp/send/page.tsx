import { redirect } from "next/navigation";

// The Send Message screen now lives directly on the AI WhatsApp landing page.
export default function WhatsAppSendRedirectPage() {
  redirect("/whatsapp");
}
