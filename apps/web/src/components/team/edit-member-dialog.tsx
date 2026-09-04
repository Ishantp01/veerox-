"use client";

import { useState } from "react";
import { Pencil } from "lucide-react";
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
  useToast,
} from "@/components/ui";
import { useUpdateMember, type TeamMember } from "@/lib/hooks/useTeam";

const E164_REGEX = /^\+\d{8,15}$/;
const E164_MESSAGE = "Enter a valid E.164 number, e.g. +919876543210";

const editSchema = z.object({
  email: z.string().trim().email(),
  fullName: z
    .string()
    .trim()
    .max(200, "Name is too long")
    .regex(/^[A-Za-z\s'.-]*$/, "Name should only contain letters")
    .optional(),
  mobile: z
    .string()
    .trim()
    .optional()
    .refine((v) => !v || E164_REGEX.test(v), E164_MESSAGE),
});

type EditFieldErrors = Partial<Record<"email" | "fullName" | "mobile", string>>;

/**
 * Admin-only edit of a teammate's own profile fields (name/email/mobile) —
 * distinct from the inline Role select, which PATCHes just the
 * OrgMembership row. This edits the underlying AccountUser directly (see
 * apps/api/schemas/team.py::UpdateMemberIn), so it changes that person's
 * profile across every org they belong to, not just this one.
 */
export function EditMemberDialog({ member }: { member: TeamMember }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState({
    email: member.email,
    fullName: member.full_name ?? "",
    mobile: member.mobile ?? "",
  });
  const [fieldErrors, setFieldErrors] = useState<EditFieldErrors>({});
  const updateMember = useUpdateMember();
  const { toast } = useToast();

  function handleOpenChange(next: boolean) {
    setOpen(next);
    if (next) {
      setForm({ email: member.email, fullName: member.full_name ?? "", mobile: member.mobile ?? "" });
      setFieldErrors({});
      updateMember.reset();
    }
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const parsed = editSchema.safeParse(form);
    if (!parsed.success) {
      const errors: EditFieldErrors = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0] as keyof EditFieldErrors;
        if (!errors[key]) errors[key] = issue.message;
      }
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});
    updateMember.mutate(
      {
        accountUserId: member.account_user_id,
        email: form.email.trim(),
        full_name: form.fullName.trim() || undefined,
        mobile: form.mobile.trim() || undefined,
      },
      {
        onSuccess: () => {
          toast({ title: "Team member updated", variant: "success" });
          setOpen(false);
        },
        onError: (err) => {
          const conflict = (err as { status?: number }).status === 409;
          toast({
            title: conflict ? "Email already in use" : "Could not update team member",
            description: conflict ? undefined : err.message,
            variant: "error",
          });
        },
      }
    );
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger>
        <Button variant="ghost" size="sm" aria-label={`Edit ${member.email}`}>
          <Pencil size={14} />
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>Edit team member</DialogTitle>
        <form onSubmit={handleSubmit} noValidate>
          <DialogBody className="flex flex-col gap-4">
            <div>
              <Label htmlFor="edit-member-name">Name</Label>
              <Input
                id="edit-member-name"
                value={form.fullName}
                onChange={(e) => setForm((f) => ({ ...f, fullName: e.target.value }))}
                placeholder="Optional"
                aria-invalid={fieldErrors.fullName ? true : undefined}
                aria-describedby={fieldErrors.fullName ? "edit-member-name-error" : undefined}
              />
              {fieldErrors.fullName && (
                <p id="edit-member-name-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.fullName}
                </p>
              )}
            </div>
            <div>
              <Label htmlFor="edit-member-email">Email *</Label>
              <Input
                id="edit-member-email"
                type="email"
                required
                value={form.email}
                onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
                aria-invalid={fieldErrors.email ? true : undefined}
                aria-describedby={fieldErrors.email ? "edit-member-email-error" : undefined}
              />
              {fieldErrors.email && (
                <p id="edit-member-email-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.email}
                </p>
              )}
            </div>
            <div>
              <Label htmlFor="edit-member-mobile">Phone number</Label>
              <Input
                id="edit-member-mobile"
                type="tel"
                value={form.mobile}
                onChange={(e) => setForm((f) => ({ ...f, mobile: e.target.value }))}
                placeholder="+919876543210"
                aria-invalid={fieldErrors.mobile ? true : undefined}
                aria-describedby={fieldErrors.mobile ? "edit-member-mobile-error" : "edit-member-mobile-hint"}
              />
              {fieldErrors.mobile ? (
                <p id="edit-member-mobile-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.mobile}
                </p>
              ) : (
                <p id="edit-member-mobile-hint" className="mt-1.5 text-xs text-slate-500">
                  Optional — lets the AI WhatsApp this teammate when a lead asks to be connected to a
                  human.
                </p>
              )}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => handleOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={updateMember.isPending}>
              Save changes
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
