import { redirect } from "next/navigation";

// Leads now live on the unified CRM leads page.
export default function LeadsRedirectPage() {
  redirect("/crm/leads");
}
