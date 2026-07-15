"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import { OutboundWhatsAppForm } from "@/components/whatsapp/outbound-form";

export default function UserDetailPage() {
  const params = useParams();
  const id =
    typeof params.id === "string" ? params.id : params.id?.[0] ?? "";

  return (
    <div className="mx-auto max-w-7xl">
      <Link
        href="/whatsapp/conversations"
        className="mb-4 inline-flex items-center gap-1.5 rounded-sm text-sm text-slate-500 transition-colors hover:text-slate-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
      >
        <ArrowLeft size={15} aria-hidden /> Back
      </Link>

      <PageHeader
        title="User"
        description={`User ID: ${id || "—"}`}
      />

      <OutboundWhatsAppForm />
    </div>
  );
}
