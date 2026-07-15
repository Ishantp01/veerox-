"use client";

import { useParams } from "next/navigation";
import { LeadDetail } from "@/components/leads/lead-detail";

export default function WhatsAppLeadDetailPage() {
  const params = useParams();
  const id = typeof params.id === "string" ? params.id : (params.id?.[0] ?? "");

  return <LeadDetail id={id} backHref="/whatsapp/leads" backLabel="Leads" />;
}
