"use client";

import { useState } from "react";
import { UserPlus } from "lucide-react";
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
import { useCreateContact } from "@/lib/hooks";

const EMPTY = { name: "", phone: "+91", email: "", company: "" };

const contactSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Name is required")
    .regex(/^[A-Za-z\s'.-]+$/, "Name should only contain letters"),
  phone: z
    .string()
    .trim()
    .regex(/^\+\d{8,15}$/, "Enter a valid E.164 number, e.g. +919876543210"),
  email: z.string().trim().email().optional().or(z.literal("")),
  company: z
    .string()
    .trim()
    .refine((v) => !/^\d+$/.test(v), "Company name cannot be only numbers")
    .optional(),
});

type ContactFieldErrors = Partial<Record<keyof typeof EMPTY, string>>;

export function NewContactDialog() {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const [fieldErrors, setFieldErrors] = useState<ContactFieldErrors>({});
  const createContact = useCreateContact();
  const { toast } = useToast();

  function validateField(key: keyof typeof EMPTY, nextForm: typeof EMPTY) {
    const result = contactSchema.safeParse(nextForm);
    if (result.success) {
      setFieldErrors((prev) => ({ ...prev, [key]: undefined }));
      return;
    }
    const issue = result.error.issues.find((i) => i.path[0] === key);
    setFieldErrors((prev) => ({ ...prev, [key]: issue?.message }));
  }

  function updateField(key: keyof typeof EMPTY, value: string) {
    const next = { ...form, [key]: value };
    setForm(next);
    validateField(key, next);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const result = contactSchema.safeParse(form);
    if (!result.success) {
      const errors: ContactFieldErrors = {};
      for (const issue of result.error.issues) {
        const key = issue.path[0] as keyof typeof EMPTY;
        if (!errors[key]) errors[key] = issue.message;
      }
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});
    createContact.mutate(
      {
        name: form.name || null,
        phone: form.phone,
        email: form.email || null,
        company: form.company || null,
      },
      {
        onSuccess: () => {
          toast({ title: "Contact created", variant: "success" });
          setForm(EMPTY);
          setFieldErrors({});
          setOpen(false);
        },
        onError: (err) =>
          toast({ title: "Could not create contact", description: err.message, variant: "error" }),
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger>
        <Button variant="primary" size="md">
          <UserPlus size={15} aria-hidden />
          New Contact
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>New contact</DialogTitle>
        <form onSubmit={handleSubmit} noValidate>
          <DialogBody className="flex flex-col gap-4">
            <div>
              <Label htmlFor="contact-phone">Phone *</Label>
              <Input
                id="contact-phone"
                type="tel"
                inputMode="tel"
                required
                maxLength={16}
                value={form.phone}
                onChange={(e) => updateField("phone", e.target.value)}
                placeholder="+91XXXXXXXXXX"
                aria-invalid={fieldErrors.phone ? true : undefined}
                aria-describedby={fieldErrors.phone ? "contact-phone-error" : undefined}
              />
              {fieldErrors.phone && (
                <p id="contact-phone-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.phone}
                </p>
              )}
            </div>
            <div>
              <Label htmlFor="contact-name">Name</Label>
              <Input
                id="contact-name"
                value={form.name}
                onChange={(e) => updateField("name", e.target.value)}
                aria-invalid={fieldErrors.name ? true : undefined}
                aria-describedby={fieldErrors.name ? "contact-name-error" : undefined}
              />
              {fieldErrors.name && (
                <p id="contact-name-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.name}
                </p>
              )}
            </div>
            <div>
              <Label htmlFor="contact-email">Email</Label>
              <Input
                id="contact-email"
                type="email"
                value={form.email}
                onChange={(e) => updateField("email", e.target.value)}
                aria-invalid={fieldErrors.email ? true : undefined}
                aria-describedby={fieldErrors.email ? "contact-email-error" : undefined}
              />
              {fieldErrors.email && (
                <p id="contact-email-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.email}
                </p>
              )}
            </div>
            <div>
              <Label htmlFor="contact-company">Company</Label>
              <Input
                id="contact-company"
                value={form.company}
                onChange={(e) => updateField("company", e.target.value)}
                aria-invalid={fieldErrors.company ? true : undefined}
                aria-describedby={fieldErrors.company ? "contact-company-error" : undefined}
              />
              {fieldErrors.company && (
                <p id="contact-company-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.company}
                </p>
              )}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={createContact.isPending}>
              Create contact
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
