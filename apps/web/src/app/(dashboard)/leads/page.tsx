import { redirect } from "next/navigation";

// Leads are now split by channel under /whatsapp/leads and /calling/leads.
export default function LeadsRedirectPage() {
  redirect("/");
}
