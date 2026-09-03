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
import { useUpdateContact } from "@/lib/hooks";
import type { Contact } from "@/lib/types";

const editContactSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Name is required")
    .regex(/^[A-Za-z\s'.-]+$/, "Name should only contain letters"),
  email: z.string().trim().email().optional().or(z.literal("")),
  company: z
    .string()
    .trim()
    .refine((v) => !/^\d+$/.test(v), "Company name cannot be only numbers")
    .optional(),
});

type EditContactForm = z.infer<typeof editContactSchema>;
type ContactFieldErrors = Partial<Record<keyof EditContactForm, string>>;

function formFromContact(contact: Contact): EditContactForm {
  return {
    name: contact.name ?? "",
    email: contact.email ?? "",
    company: contact.company ?? "",
  };
}

/** Edits a contact's name, email, and company. Phone isn't editable — it's the contact's unique identity key. */
export function EditContactDialog({ contact }: { contact: Contact }) {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<EditContactForm>(() => formFromContact(contact));
  const [fieldErrors, setFieldErrors] = useState<ContactFieldErrors>({});
  const updateContact = useUpdateContact();
  const { toast } = useToast();

  function validateField(key: keyof EditContactForm, nextForm: EditContactForm) {
    const result = editContactSchema.safeParse(nextForm);
    if (result.success) {
      setFieldErrors((prev) => ({ ...prev, [key]: undefined }));
      return;
    }
    const issue = result.error.issues.find((i) => i.path[0] === key);
    setFieldErrors((prev) => ({ ...prev, [key]: issue?.message }));
  }

  function updateField(key: keyof EditContactForm, value: string) {
    const next = { ...form, [key]: value };
    setForm(next);
    validateField(key, next);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const parsed = editContactSchema.safeParse(form);
    if (!parsed.success) {
      const errors: ContactFieldErrors = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0] as keyof EditContactForm;
        if (!errors[key]) errors[key] = issue.message;
      }
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});
    updateContact.mutate(
      {
        id: contact.id,
        name: parsed.data.name.trim(),
        email: parsed.data.email?.trim() || null,
        company: parsed.data.company?.trim() || null,
      },
      {
        onSuccess: () => {
          toast({ title: "Contact updated", variant: "success" });
          handleClose(false);
        },
        onError: (err) =>
          toast({ title: "Could not update contact", description: err.message, variant: "error" }),
      },
    );
  }

  function handleClose(next: boolean) {
    setOpen(next);
    if (next) {
      setForm(formFromContact(contact));
    } else {
      setFieldErrors({});
      updateContact.reset();
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogTrigger>
        <Button variant="outline" size="sm" aria-label={`Edit ${contact.name ?? contact.phone}`}>
          <Pencil size={14} aria-hidden />
          Edit contact
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>Edit contact</DialogTitle>
        <form onSubmit={handleSubmit} noValidate>
          <DialogBody className="flex flex-col gap-4">
            <div>
              <Label htmlFor="edit-contact-name">Name *</Label>
              <Input
                id="edit-contact-name"
                required
                value={form.name}
                onChange={(e) => updateField("name", e.target.value)}
                aria-invalid={fieldErrors.name ? true : undefined}
                aria-describedby={fieldErrors.name ? "edit-contact-name-error" : undefined}
              />
              {fieldErrors.name && (
                <p id="edit-contact-name-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.name}
                </p>
              )}
            </div>
            <div>
              <Label htmlFor="edit-contact-email">Email</Label>
              <Input
                id="edit-contact-email"
                type="email"
                value={form.email}
                onChange={(e) => updateField("email", e.target.value)}
                aria-invalid={fieldErrors.email ? true : undefined}
                aria-describedby={fieldErrors.email ? "edit-contact-email-error" : undefined}
              />
              {fieldErrors.email && (
                <p id="edit-contact-email-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.email}
                </p>
              )}
            </div>
            <div>
              <Label htmlFor="edit-contact-company">Company</Label>
              <Input
                id="edit-contact-company"
                value={form.company}
                onChange={(e) => updateField("company", e.target.value)}
                aria-invalid={fieldErrors.company ? true : undefined}
                aria-describedby={fieldErrors.company ? "edit-contact-company-error" : undefined}
              />
              {fieldErrors.company && (
                <p id="edit-contact-company-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.company}
                </p>
              )}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => handleClose(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={updateContact.isPending}>
              Save changes
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
