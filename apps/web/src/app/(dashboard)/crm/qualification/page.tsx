import { redirect } from "next/navigation";

/**
 * Qualification is now a filter on the Leads page, not a separate view —
 * status and qualification_status are two fields on the same Lead row (see
 * lead-table.tsx). Redirect any bookmarked/linked traffic there.
 */
export default function QualificationPage() {
  redirect("/crm/leads");
}
