"use client";

import { useState } from "react";
import { Plus } from "lucide-react";
import { z } from "zod";
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
  Select,
  useToast,
} from "@/components/ui";
import { useInviteMember, type InviteMemberResult } from "@/lib/hooks/useTeam";

const EMPTY = { email: "", fullName: "", role: "member" };

const inviteSchema = z.object({
  email: z.string().trim().email(),
  fullName: z
    .string()
    .trim()
    .max(200, "Name is too long")
    .regex(/^[A-Za-z\s'.-]*$/, "Name should only contain letters")
    .optional(),
  role: z.string().trim().min(1),
});

type InviteFieldErrors = Partial<Record<"email" | "fullName" | "role", string>>;

/**
 * Admin self-service invite — the org's own equivalent of
 * NewOrgDialog, scoped to the caller's org instead of the whole platform
 * (see apps/api/routers/team.py). A brand new login token is only shown
 * when the invite creates a fresh account; an email that already has one
 * elsewhere just gets added to this org on its existing login.
 */
export function InviteMemberDialog({ disabled = false }: { disabled?: boolean }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [fieldErrors, setFieldErrors] = useState<InviteFieldErrors>({});
  const [result, setResult] = useState<InviteMemberResult | null>(null);
  const inviteMember = useInviteMember();
  const { toast } = useToast();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const parsed = inviteSchema.safeParse(form);
    if (!parsed.success) {
      const errors: InviteFieldErrors = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0] as keyof InviteFieldErrors;
        if (!errors[key]) errors[key] = issue.message;
      }
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});
    inviteMember.mutate(
      {
        email: form.email.trim(),
        full_name: form.fullName.trim() || undefined,
        role: form.role,
      },
      {
        onSuccess: (res) => {
          setResult(res);
          toast({ title: "Team member added", variant: "success" });
        },
        onError: (err) => {
          const limitReached = (err as { status?: number }).status === 402;
          toast({
            title: limitReached ? "Team member limit reached" : "Could not add team member",
            description: limitReached
              ? "Your plan doesn't allow any more team members — upgrade to add more."
              : err.message,
            variant: "error",
          });
        },
      }
    );
  }

  function handleClose(next: boolean) {
    setOpen(next);
    if (!next) {
      setForm(EMPTY);
      setFieldErrors({});
      setResult(null);
      inviteMember.reset();
    }
  }

  // A disabled Button never fires a click event (browsers suppress it), so a
  // limit-reached org clicking "Add team member" would see nothing happen.
  // Keep the button enabled and intercept the click with a toast instead —
  // that's the "sensible" feedback a disabled+tooltip button can't give.
  if (disabled) {
    return (
      <Button
        variant="primary"
        size="md"
        onClick={() =>
          toast({
            title: "Team member limit reached",
            description: "Your plan doesn't allow any more team members — upgrade to add more.",
            variant: "error",
          })
        }
      >
        <Plus size={15} aria-hidden />
        Add team member
      </Button>
    );
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogTrigger>
        <Button variant="primary" size="md">
          <Plus size={15} aria-hidden />
          Add team member
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>Add team member</DialogTitle>
        {result ? (
          <>
            <DialogBody className="flex flex-col gap-3">
              {result.login_token ? (
                <>
                  <p>
                    Give both of these to <strong>{result.email}</strong> — the token is their only
                    way to sign in, and it won&apos;t be shown again.
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
                </>
              ) : (
                <p>
                  <strong>{result.email}</strong> already had an account on Veerox and has been added
                  to this team as <strong>{result.role}</strong> — they can sign in with their
                  existing login token.
                </p>
              )}
            </DialogBody>
            <DialogFooter>
              <Button variant="primary" onClick={() => handleClose(false)}>
                Done
              </Button>
            </DialogFooter>
          </>
        ) : (
          <form onSubmit={handleSubmit} noValidate>
            <DialogBody className="flex flex-col gap-4">
              <div>
                <Label htmlFor="member-email">Email *</Label>
                <Input
                  id="member-email"
                  type="email"
                  required
                  value={form.email}
                  onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
                  placeholder="teammate@company.com"
                  aria-invalid={fieldErrors.email ? true : undefined}
                  aria-describedby={fieldErrors.email ? "member-email-error" : undefined}
                />
                {fieldErrors.email && (
                  <p id="member-email-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.email}
                  </p>
                )}
              </div>
              <div>
                <Label htmlFor="member-name">Name</Label>
                <Input
                  id="member-name"
                  value={form.fullName}
                  onChange={(e) => setForm((f) => ({ ...f, fullName: e.target.value }))}
                  placeholder="Optional"
                  aria-invalid={fieldErrors.fullName ? true : undefined}
                  aria-describedby={fieldErrors.fullName ? "member-name-error" : undefined}
                />
                {fieldErrors.fullName && (
                  <p id="member-name-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.fullName}
                  </p>
                )}
              </div>
              <div>
                <Label htmlFor="member-role">Role</Label>
                <Select
                  value={form.role}
                  onChange={(role) => setForm((f) => ({ ...f, role }))}
                  className="w-full"
                >
                  <option value="member">Member</option>
                  <option value="admin">Admin</option>
                </Select>
              </div>
            </DialogBody>
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => handleClose(false)}>
                Cancel
              </Button>
              <Button type="submit" variant="primary" loading={inviteMember.isPending}>
                Add member
              </Button>
            </DialogFooter>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
