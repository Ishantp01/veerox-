import { redirect } from "next/navigation";

// Voice leads are now shown by the unified CRM leads page, pre-filtered to
// this channel — avoids running two separate LeadsView instances for the
// same underlying data.
export default function CallingLeadsPage() {
  redirect("/crm/leads?channel=voice");
}
