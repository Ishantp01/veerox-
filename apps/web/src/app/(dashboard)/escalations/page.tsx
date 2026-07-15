import { redirect } from "next/navigation";

// Escalations are now split by channel under /whatsapp/escalations and
// /calling/escalations.
export default function EscalationsRedirectPage() {
  redirect("/");
}
