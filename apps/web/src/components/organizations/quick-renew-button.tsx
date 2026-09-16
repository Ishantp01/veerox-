"use client";

import { RotateCw } from "lucide-react";
import { Button, useToast } from "@/components/ui";
import { useRenewLicense, type AdminOrg } from "@/lib/hooks/useAdminOrgs";

const DEFAULT_RENEW_DAYS = 30;

/**
 * One-click license renewal for the Organizations row actions — no dialog,
 * for the common case (a client paid for another period, nothing else to
 * change). Reuses whatever duration this org was last issued/renewed for
 * (Org.license_duration_days, resolved server-side in
 * routers/billing.py's renew_license), falling back to 30 days for an org
 * that's never had one recorded. ManageLicenseDialog (the key icon) still
 * covers everything else — a custom duration, notes, suspend, or reactivate.
 */
export function QuickRenewButton({ org }: { org: AdminOrg }) {
  const renewLicense = useRenewLicense();
  const { toast } = useToast();
  const days = org.license_duration_days ?? DEFAULT_RENEW_DAYS;

  function handleClick() {
    renewLicense.mutate(
      { orgId: org.id },
      {
        onSuccess: () => toast({ title: `License renewed for ${days} days`, variant: "success" }),
        onError: (err) =>
          toast({ title: "Could not renew license", description: err.message, variant: "error" }),
      }
    );
  }

  return (
    <Button
      variant="ghost"
      size="sm"
      aria-label={`Renew ${org.name} for ${days} days`}
      title={`Renew for ${days} days`}
      loading={renewLicense.isPending}
      onClick={handleClick}
    >
      {!renewLicense.isPending && <RotateCw size={14} />}
    </Button>
  );
}
