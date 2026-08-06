"use client";

import { useState } from "react";
import { UserPlus } from "lucide-react";
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

export function NewContactDialog() {
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const createContact = useCreateContact();
  const { toast } = useToast();

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
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
        <form onSubmit={handleSubmit}>
          <DialogBody className="flex flex-col gap-4">
            <div>
              <Label htmlFor="contact-phone">Phone *</Label>
              <Input
                id="contact-phone"
                required
                value={form.phone}
                onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))}
                placeholder="+91XXXXXXXXXX"
              />
            </div>
            <div>
              <Label htmlFor="contact-name">Name</Label>
              <Input
                id="contact-name"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              />
            </div>
            <div>
              <Label htmlFor="contact-email">Email</Label>
              <Input
                id="contact-email"
                type="email"
                value={form.email}
                onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
              />
            </div>
            <div>
              <Label htmlFor="contact-company">Company</Label>
              <Input
                id="contact-company"
                value={form.company}
                onChange={(e) => setForm((f) => ({ ...f, company: e.target.value }))}
              />
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
