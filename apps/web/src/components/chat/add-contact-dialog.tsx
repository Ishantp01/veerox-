"use client";

import { useState } from "react";
import { z } from "zod";
import {
  Button,
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogTitle,
  Input,
  Label,
  useToast,
} from "@/components/ui";
import { useCreateContact, useOrgCountryCode } from "@/lib/hooks";
import { normalizePhone } from "@/lib/phone";

const schema = z.object({
  name: z
    .string()
    .trim()
    .regex(/^[A-Za-z\s'.-]*$/, "Name should only contain letters"),
  email: z.string().trim().email("Enter a valid email").optional().or(z.literal("")),
  company: z
    .string()
    .trim()
    .refine((v) => !/^\d+$/.test(v), "Company name cannot be only numbers"),
});

type Errors = Partial<Record<"name" | "email" | "company", string>>;

/** Saves the chat's number as a CRM contact, with the name / email / company /
 * tags the user types in. The phone is fixed — it's the number being chatted with. */
export function AddContactDialog({
  phone,
  initialName,
  open,
  onOpenChange,
  onCreated,
}: {
  phone: string;
  initialName: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: () => void;
}) {
  const countryCode = useOrgCountryCode();
  const createContact = useCreateContact();
  const { toast } = useToast();
  const [name, setName] = useState(initialName?.trim() ?? "");
  const [email, setEmail] = useState("");
  const [company, setCompany] = useState("");
  const [tags, setTags] = useState("");
  const [errors, setErrors] = useState<Errors>({});

  function submit(e: React.FormEvent) {
    e.preventDefault();
    const parsed = schema.safeParse({ name, email, company });
    if (!parsed.success) {
      const next: Errors = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0] as keyof Errors;
        if (!next[key]) next[key] = issue.message;
      }
      setErrors(next);
      return;
    }
    setErrors({});
    const tagList = tags
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);
    createContact.mutate(
      {
        name: name.trim() || null,
        phone: normalizePhone(phone, countryCode),
        email: email.trim() || null,
        company: company.trim() || null,
        tags: tagList.length ? tagList : null,
      },
      {
        onSuccess: () => {
          toast({ title: "Added to contacts", variant: "success" });
          onCreated();
          onOpenChange(false);
        },
        onError: (err) =>
          toast({ title: "Couldn't add contact", description: err.message, variant: "error" }),
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>Add to contacts</DialogTitle>
        <form onSubmit={submit} noValidate>
          <DialogBody className="flex flex-col gap-4">
            <div>
              <Label htmlFor="chat-contact-phone">Phone</Label>
              <Input id="chat-contact-phone" value={phone} readOnly disabled />
            </div>
            <div>
              <Label htmlFor="chat-contact-name">Name</Label>
              <Input
                id="chat-contact-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                aria-invalid={errors.name ? true : undefined}
              />
              {errors.name && <p className="mt-1.5 text-xs text-red-600">{errors.name}</p>}
            </div>
            <div>
              <Label htmlFor="chat-contact-email">Email</Label>
              <Input
                id="chat-contact-email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                aria-invalid={errors.email ? true : undefined}
              />
              {errors.email && <p className="mt-1.5 text-xs text-red-600">{errors.email}</p>}
            </div>
            <div>
              <Label htmlFor="chat-contact-company">Company</Label>
              <Input
                id="chat-contact-company"
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                aria-invalid={errors.company ? true : undefined}
              />
              {errors.company && <p className="mt-1.5 text-xs text-red-600">{errors.company}</p>}
            </div>
            <div>
              <Label htmlFor="chat-contact-tags">Tags</Label>
              <Input
                id="chat-contact-tags"
                value={tags}
                onChange={(e) => setTags(e.target.value)}
                placeholder="Comma separated, e.g. hot, referral"
              />
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={createContact.isPending}>
              Save contact
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
