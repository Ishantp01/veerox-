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
import { SetupInstructionsView } from "./setup-instructions-view";
import { useRegenerateAdminToken, type RegenerateAdminTokenResult } from "@/lib/hooks/useAdminOrgs";
import { useRegenerateSetupToken, type SetupInstructions } from "@/lib/hooks/usePlatformOrgs";

export interface RegenerateTokenOrg {
  id: string;
  name: string;
  // Which kind of credential this org actually has — an org has exactly
  // one of these, never both: a shared-platform org has a real admin login
  // (admin_email set); a separate-server org has a deployment setup token
  // instead (client_deployment_id set). Neither is trying to reach into
  // the other's data — the branch below just picks which existing action
  // applies to THIS org.
  admin_email: string | null;
  client_deployment_id: string | null;
}

/**
 * One "regenerate token" action that works for any organization, whichever
 * kind of credential it actually has:
 *   - A shared-platform org (real admin login): rotates its login token
 *     (POST /billing/orgs/{id}/regenerate-admin-token).
 *   - A separate-server org (its own hosting): rotates its deployment setup
 *     token (POST /platform/organizations/{id}/deployments/regenerate-token
 *     — the same action available from the Hosting setup dialog, exposed
 *     here too as a one-click shortcut).
 * Neither token can ever be shown again once generated — only its hash is
 * stored server-side — so this always issues a brand new one rather than
 * displaying the old one.
 */
export function RegenerateTokenDialog({ org }: { org: RegenerateTokenOrg }) {
  const hasAdmin = !!org.admin_email;
  const hasDeployment = !!org.client_deployment_id;

  if (!hasAdmin && !hasDeployment) return null;

  return hasAdmin ? <RegenerateLoginToken orgId={org.id} orgName={org.name} /> : <RegenerateSetupToken orgId={org.id} orgName={org.name} />;
}

function RegenerateLoginToken({ orgId, orgName }: { orgId: string; orgName: string }) {
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
        <Button
          variant="ghost"
          size="sm"
          aria-label={`Regenerate login token for ${orgName}`}
          title="Regenerate login token"
        >
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

function RegenerateSetupToken({ orgId, orgName }: { orgId: string; orgName: string }) {
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<SetupInstructions | null>(null);
  const regenerate = useRegenerateSetupToken();
  const { toast } = useToast();

  function handleConfirm() {
    regenerate.mutate(orgId, {
      onSuccess: (res) => {
        setResult(res);
        toast({ title: "New setup token issued", variant: "success" });
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
        <Button
          variant="ghost"
          size="sm"
          aria-label={`Regenerate setup token for ${orgName}`}
          title="Regenerate setup token"
        >
          <KeyRound size={14} />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>Regenerate setup token</DialogTitle>
        {result ? (
          <>
            <DialogBody className="flex flex-col gap-3">
              <p>
                <strong>{orgName}</strong>&apos;s previous setup token stopped working just now —
                send this new configuration to whoever manages its server.
              </p>
              <SetupInstructionsView setup={result} />
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
                This immediately invalidates <strong>{orgName}</strong>&apos;s current setup token —
                its server will stop being able to validate its licence until it&apos;s updated with
                the new one and restarted. Use this only if the original token was lost or
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
