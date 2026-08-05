"use client";

import { useState } from "react";
import { KeyRound } from "lucide-react";
import {
  Button,
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogTitle,
  DialogBody,
  DialogFooter,
  Label,
  useToast,
} from "@/components/ui";
import { useRegenerateAdminToken, type RegenerateAdminTokenResult } from "@/lib/hooks/useAdminOrgs";

/**
 * There's no "see token" — only a SHA-256 hash of the original is ever
 * stored (see apps/api/routers/billing.py's regenerate_admin_token), so the
 * token an org's admin was given at creation can't be displayed again. This
 * issues a brand new one instead, invalidating the old one immediately.
 */
export function RegenerateTokenDialog({ orgId, orgName }: { orgId: string; orgName: string }) {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<RegenerateAdminTokenResult | null>(null);
  const regenerate = useRegenerateAdminToken();
  const { toast } = useToast();

  function handleConfirm() {
    regenerate.mutate(orgId, {
      onSuccess: (res) => {
        setResult(res);
        toast({ title: "New login token issued", variant: "success" });
      },
      onError: (err) =>
        toast({ title: "Could not regenerate token", description: err.message, variant: "error" }),
    });
  }

  function handleClose(next: boolean) {
    setOpen(next);
    if (!next) {
      setResult(null);
      regenerate.reset();
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogTrigger>
        <Button variant="ghost" size="sm" aria-label={`Regenerate login token for ${orgName}`}>
          <KeyRound size={14} />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>Regenerate login token</DialogTitle>
        {result ? (
          <>
            <DialogBody className="flex flex-col gap-3">
              <p>
                Give this to <strong>{result.email}</strong> — their previous token stopped working
                just now, and this new one won&apos;t be shown again.
              </p>
              <div>
                <Label>New login token</Label>
                <code className="block break-all rounded-lg bg-slate-100 px-3 py-2 text-xs dark:bg-slate-800">
                  {result.login_token}
                </code>
              </div>
            </DialogBody>
            <DialogFooter>
              <Button variant="primary" onClick={() => handleClose(false)}>
                Done
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogBody className="flex flex-col gap-3">
              <p>
                This immediately invalidates <strong>{orgName}</strong>&apos;s current login token and
                any active sessions for its admin. Use this only if the original token was lost or
                compromised.
              </p>
            </DialogBody>
            <DialogFooter>
              <Button variant="outline" onClick={() => handleClose(false)}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleConfirm} loading={regenerate.isPending}>
                Regenerate token
              </Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
