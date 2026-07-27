"use client";

import { useParams } from "next/navigation";
import { ContactDetail } from "@/components/crm/contact-detail";

export default function ContactDetailPage() {
  const params = useParams();
  const id = typeof params.id === "string" ? params.id : (params.id?.[0] ?? "");

  return <ContactDetail id={id} />;
}
