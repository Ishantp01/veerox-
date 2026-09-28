"use client";

import { useState } from "react";
import { KeyRound } from "lucide-react";
import {
  Badge,
  Button,
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogTitle,
  DialogBody,
  DialogFooter,
  Input,
  Label,
  Textarea,
  useToast,
} from "@/components/ui";
import {
  useExtendLicense,
  useIssueLicense,
  useReactivateLicense,
  useRenewLicense,
  useSuspendLicense,
  type AdminOrg,
} from "@/lib/hooks/useAdminOrgs";

const STATUS_BADGE: Record<AdminOrg["license_status"], "success" | "danger" | "neutral"> = {
  active: "success",
  suspended: "danger",
  expired: "danger",
};

type Action = "issue" | "renew" | "extend" | "suspend" | "reactivate";

const ACTIONS_BY_STATUS: Record<AdminOrg["license_status"], Action[]> = {
  active: ["renew", "extend", "suspend"],
  suspended: ["reactivate"],
  expired: ["renew", "reactivate"],
};

const ACTION_LABEL: Record<Action, string> = {
  issue: "Issue license",
  renew: "Renew",
  extend: "Extend",
  suspend: "Suspend",
  reactivate: "Reactivate",
};

/**
 * Platform-admin-only: issue/renew/extend/suspend/reactivate an org's
 * license (see POST /billing/orgs/{id}/license/* in apps/api/routers/billing.py).
 * Every duration here is entered as a number of days from now, not a
 * calendar date — matches how licenses are actually sold/renewed off-platform
 * ("30 days", "1 year"), and avoids the timezone ambiguity of a bare date.
 */
export function ManageLicenseDialog({ org }: { org: AdminOrg }) {
  const [open, setOpen] = useState(false);
  const [action, setAction] = useState<Action>(
    org.license_status === "active" ? "renew" : ACTIONS_BY_STATUS[org.license_status][0]
  );
  const [days, setDays] = useState(String(org.license_duration_days ?? 30));
  // Only meaningful for "reactivate": whether the admin actually edited the
  // pre-filled duration, vs. it just sitting at its default — determines
  // whether we send an override `days` or let the backend keep the
  // existing expiry untouched (see handleSubmit's "reactivate" case).
  const [daysTouched, setDaysTouched] = useState(false);
  const [notes, setNotes] = useState(org.license_notes ?? "");
  const { toast } = useToast();

  const issueLicense = useIssueLicense();
  const renewLicense = useRenewLicense();
  const extendLicense = useExtendLicense();
  const suspendLicense = useSuspendLicense();
  const reactivateLicense = useReactivateLicense();

  const hasNoLicenseYet = org.license_status === "active" && org.license_expires_at === null;
  const availableActions = hasNoLicenseYet ? ["issue" as Action] : ACTIONS_BY_STATUS[org.license_status];
  const pending =
    issueLicense.isPending ||
    renewLicense.isPending ||
    extendLicense.isPending ||
    suspendLicense.isPending ||
    reactivateLicense.isPending;

  function handleClose(next: boolean) {
    setOpen(next);
    if (!next) {
      setDays(String(org.license_duration_days ?? 30));
      setDaysTouched(false);
      setNotes(org.license_notes ?? "");
      issueLicense.reset();
      renewLicense.reset();
      extendLicense.reset();
      suspendLicense.reset();
      reactivateLicense.reset();
    } else {
      setAction(hasNoLicenseYet ? "issue" : availableActions[0]);
    }
  }

  function onSuccess(message: string) {
    toast({ title: message, variant: "success" });
    handleClose(false);
  }
  function onError(err: Error) {
    toast({ title: "Could not update license", description: err.message, variant: "error" });
  }

  const parsedDays = Number(days);
  const validDays = Number.isFinite(parsedDays) && parsedDays > 0;

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    switch (action) {
      case "issue":
        if (!validDays) return;
        issueLicense.mutate(
          { orgId: org.id, days: parsedDays, notes: notes.trim() || undefined },
          { onSuccess: () => onSuccess("License issued"), onError }
        );
        return;
      case "renew":
        if (!validDays) return;
        renewLicense.mutate(
          { orgId: org.id, days: parsedDays },
          { onSuccess: () => onSuccess("License renewed"), onError }
        );
        return;
      case "extend":
        if (!validDays) return;
        extendLicense.mutate(
          { orgId: org.id, days: parsedDays },
          { onSuccess: () => onSuccess("License extended"), onError }
        );
        return;
      case "suspend":
        suspendLicense.mutate(
          { orgId: org.id, notes: notes.trim() || undefined },
          { onSuccess: () => onSuccess("Organization suspended"), onError }
        );
        return;
      case "reactivate":
        reactivateLicense.mutate(
          // The backend keeps the existing expiry (or falls back to the
          // org's last duration once it's already passed) when `days` is
          // omitted — only send an override when the admin actually
          // changed the pre-filled value, or the expiry already lapsed.
          {
            orgId: org.id,
            days: reactivateExpiryHasPassed || daysTouched ? parsedDays : undefined,
          },
          { onSuccess: () => onSuccess("License reactivated"), onError }
        );
        return;
    }
  }

  const needsDays = action === "issue" || action === "renew" || action === "extend";
  // Reactivating always shows the (optional) duration field so the admin
  // can override it, but only truly *needs* one when the old expiry has
  // already passed — mirrored for wording only, the backend never blocks
  // this the way it used to.
  const reactivateExpiryHasPassed =
    action === "reactivate" && !!org.license_expires_at && new Date(org.license_expires_at) <= new Date();

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogTrigger>
        <Button variant="ghost" size="sm" aria-label={`Manage license for ${org.name}`}>
          <KeyRound size={14} />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle className="flex items-center gap-2.5">
          License — {org.name}
          <Badge variant={STATUS_BADGE[org.license_status]}>{org.license_status}</Badge>
        </DialogTitle>
        <form onSubmit={handleSubmit}>
          <DialogBody className="flex flex-col gap-4">
            {org.license_expires_at && (
              <p className="text-sm text-slate-600 dark:text-slate-400">
                {org.license_status === "expired" ? "Expired" : "Expires"} on{" "}
                {new Date(org.license_expires_at).toLocaleDateString()}
              </p>
            )}

            <div>
              <Label htmlFor="license-action">Action</Label>
              <select
                id="license-action"
                value={action}
                onChange={(e) => setAction(e.target.value as Action)}
                className="mt-1 w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              >
                {availableActions.map((a) => (
                  <option key={a} value={a}>
                    {ACTION_LABEL[a]}
                  </option>
                ))}
              </select>
            </div>

            {(needsDays || action === "reactivate") && (
              <div>
                <Label htmlFor="license-days">
                  {action === "extend" ? "Extend by (days)" : "License duration (days)"}
                  {action !== "reactivate" && " *"}
                </Label>
                <Input
                  id="license-days"
                  type="number"
                  min={1}
                  required={action !== "reactivate"}
                  value={days}
                  onChange={(e) => {
                    setDays(e.target.value);
                    setDaysTouched(true);
                  }}
                />
                {action === "reactivate" && (
                  <p className="mt-1.5 text-xs text-slate-500 dark:text-slate-400">
                    {reactivateExpiryHasPassed
                      ? "The current expiry has already passed — this duration starts counting from now."
                      : "Optional — leave as-is to keep the current expiry unchanged."}
                  </p>
                )}
              </div>
            )}

            {(action === "issue" || action === "suspend") && (
              <div>
                <Label htmlFor="license-notes">Notes</Label>
                <Textarea
                  id="license-notes"
                  value={notes}
                  onChange={(e) => setNotes(e.target.value)}
                  placeholder="Optional — e.g. how this was paid, or why it was suspended"
                  rows={2}
                />
              </div>
            )}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => handleClose(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant={action === "suspend" ? "danger" : "primary"}
              loading={pending}
              disabled={needsDays && !validDays}
            >
              {ACTION_LABEL[action]}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
