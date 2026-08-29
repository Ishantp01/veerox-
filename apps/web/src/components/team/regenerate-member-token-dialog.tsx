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
import {
  useRegenerateMemberToken,
  type RegenerateMemberTokenResult,
} from "@/lib/hooks/useTeam";

/**
 * Recovery path for a teammate's lost/compromised login token — there's no
 * password reset (see apps/api/core/security.py), so this issues a brand new
 * token and invalidates their current sessions immediately. Mirrors
 * components/organizations/regenerate-token-dialog.tsx, scoped to a team
 * member instead of an org's platform-admin-provisioned admin (see
 * apps/api/routers/team.py).
 */
export function RegenerateMemberTokenDialog({
  accountUserId,
  email,
}: {
  accountUserId: string;
  email: string;
}) {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<RegenerateMemberTokenResult | null>(null);
  const regenerate = useRegenerateMemberToken();
  const { toast } = useToast();

  function handleConfirm() {
    regenerate.mutate(accountUserId, {
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
        <Button variant="ghost" size="sm" aria-label={`Regenerate login token for ${email}`}>
          <KeyRound size={14} />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>Regenerate login token</DialogTitle>
        {result ? (
          <>
            <DialogBody className="flex flex-col items-center gap-3 text-center">
              <p>
                Give this to <strong>{result.email}</strong> — their previous token stopped working
                just now, and this new one won&apos;t be shown again.
              </p>
              <div className="w-full">
                <Label className="block text-center">New login token</Label>
                <code className="block break-all rounded-lg bg-slate-100 px-3 py-2 text-xs dark:bg-slate-800">
                  {result.login_token}
                </code>
              </div>
            </DialogBody>
            <DialogFooter className="justify-center">
              <Button variant="primary" onClick={() => handleClose(false)}>
                Done
              </Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogBody className="flex flex-col gap-3">
              <p>
                This immediately invalidates <strong>{email}</strong>&apos;s current login token and
                any active sessions. Use this only if the original token was lost or compromised.
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
