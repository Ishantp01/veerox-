"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import {
  Button,
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogTitle,
  DialogBody,
  DialogFooter,
  Input,
  Label,
  useToast,
} from "@/components/ui";
import { useProvisionOrg, type ProvisionOrgResult } from "@/lib/hooks/useAdminOrgs";

const EMPTY = {
  orgName: "",
  email: "",
  fullName: "",
  mobile: "",
  plivoNumber: "",
  whatsappNumberId: "",
};

/**
 * Platform-admin-only: creates a brand new org + admin account. There's no
 * self-registration (see apps/api/routers/auth.py's provision_org) — this
 * dialog is the only way a new customer org comes into existence. The
 * generated login token is the admin's sole credential and is shown exactly
 * once here; no email provider is wired up, so it must be copied and
 * handed to them directly.
 */
export function NewOrgDialog() {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [result, setResult] = useState<ProvisionOrgResult | null>(null);
  const provisionOrg = useProvisionOrg();
  const { toast } = useToast();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    provisionOrg.mutate(
      {
        org_name: form.orgName.trim(),
        email: form.email.trim(),
        full_name: form.fullName.trim() || undefined,
        mobile: form.mobile.trim(),
        plivo_phone_number: form.plivoNumber.trim() || undefined,
        whatsapp_phone_number_id: form.whatsappNumberId.trim() || undefined,
      },
      {
        onSuccess: (res) => {
          setResult(res);
          toast({ title: "Organization created", variant: "success" });
        },
        onError: (err) =>
          toast({ title: "Could not create organization", description: err.message, variant: "error" }),
      }
    );
  }

  function handleClose(next: boolean) {
    setOpen(next);
    if (!next) {
      setForm(EMPTY);
      setResult(null);
      provisionOrg.reset();
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogTrigger>
        <Button variant="primary" size="md">
          <Plus size={15} aria-hidden />
          New organization
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>New organization</DialogTitle>
        {result ? (
          <>
            <DialogBody className="flex flex-col gap-3">
              <p>
                Give both of these to <strong>{result.email}</strong> — the token is their only way to
                sign in, and it won&apos;t be shown again.
              </p>
              <p className="text-sm text-slate-500">
                {result.sms_sent
                  ? "The token was also texted to the mobile number provided."
                  : "Could not send the token by SMS — share it manually."}
              </p>
              <div>
                <Label>Email</Label>
                <code className="block break-all rounded-lg bg-slate-100 px-3 py-2 text-xs dark:bg-slate-800">
                  {result.email}
                </code>
              </div>
              <div>
                <Label>Login token</Label>
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
          <form onSubmit={handleSubmit}>
            <DialogBody className="flex flex-col gap-4">
              <div>
                <Label htmlFor="org-name">Organization name *</Label>
                <Input
                  id="org-name"
                  required
                  value={form.orgName}
                  onChange={(e) => setForm((f) => ({ ...f, orgName: e.target.value }))}
                  placeholder="Acme Inc."
                />
              </div>
              <div>
                <Label htmlFor="admin-email">Admin email *</Label>
                <Input
                  id="admin-email"
                  type="email"
                  required
                  value={form.email}
                  onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
                  placeholder="admin@acme.com"
                />
              </div>
              <div>
                <Label htmlFor="admin-name">Admin name</Label>
                <Input
                  id="admin-name"
                  value={form.fullName}
                  onChange={(e) => setForm((f) => ({ ...f, fullName: e.target.value }))}
                  placeholder="Optional"
                />
              </div>
              <div>
                <Label htmlFor="admin-mobile">Admin mobile number *</Label>
                <Input
                  id="admin-mobile"
                  type="tel"
                  required
                  value={form.mobile}
                  onChange={(e) => setForm((f) => ({ ...f, mobile: e.target.value }))}
                  placeholder="+919876543210"
                />
              </div>
              <div>
                <Label htmlFor="org-plivo-number">Dedicated calling number</Label>
                <Input
                  id="org-plivo-number"
                  type="tel"
                  value={form.plivoNumber}
                  onChange={(e) => setForm((f) => ({ ...f, plivoNumber: e.target.value }))}
                  placeholder="Optional — leave blank to use the default number"
                />
                <p className="mt-1 text-xs text-slate-500">
                  Either a Plivo or a Twilio number — we detect which provider owns it automatically.
                </p>
              </div>
              <div>
                <Label htmlFor="org-whatsapp-number">Dedicated WhatsApp number ID</Label>
                <Input
                  id="org-whatsapp-number"
                  value={form.whatsappNumberId}
                  onChange={(e) => setForm((f) => ({ ...f, whatsappNumberId: e.target.value }))}
                  placeholder="Optional — leave blank to use the default number"
                />
              </div>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => handleClose(false)}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" loading={provisionOrg.isPending}>
                Create organization
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
