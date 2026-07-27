"use client";

import { useState } from "react";
import { CalendarPlus } from "lucide-react";
import {
  Button,
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogTitle,
  DialogBody,
  DialogFooter,
  Label,
  Textarea,
  useToast,
} from "@/components/ui";
import { ContactPicker } from "@/components/crm/contact-picker";
import { useCreateAppointment } from "@/lib/hooks";
import type { Contact } from "@/lib/types";

export function NewAppointmentDialog() {
  const [open, setOpen] = useState(false);
  const [contact, setContact] = useState<Contact | null>(null);
  const [scheduledAt, setScheduledAt] = useState("");
  const [duration, setDuration] = useState("30");
  const [notes, setNotes] = useState("");
  const createAppointment = useCreateAppointment();
  const { toast } = useToast();

  function reset() {
    setContact(null);
    setScheduledAt("");
    setDuration("30");
    setNotes("");
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!scheduledAt) return;

    createAppointment.mutate(
      {
        contact_id: contact?.id ?? null,
        scheduled_at: new Date(scheduledAt).toISOString(),
        duration_minutes: Number(duration) || 30,
        notes: notes.trim() ? notes.trim() : null,
      },
      {
        onSuccess: () => {
          toast({ title: "Appointment booked", variant: "success" });
          reset();
          setOpen(false);
        },
        onError: (err) =>
          toast({ title: "Could not book appointment", description: err.message, variant: "error" }),
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger>
        <Button variant="primary" size="md">
          <CalendarPlus size={15} aria-hidden />
          New Appointment
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>Book an appointment</DialogTitle>
        <form onSubmit={handleSubmit}>
          <DialogBody className="flex flex-col gap-4">
            <div>
              <Label htmlFor="appointment-contact">Contact</Label>
              <ContactPicker value={contact} onChange={setContact} />
            </div>
            <div>
              <Label htmlFor="appointment-time">Date &amp; time *</Label>
              <input
                id="appointment-time"
                type="datetime-local"
                required
                value={scheduledAt}
                onChange={(e) => setScheduledAt(e.target.value)}
                className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              />
            </div>
            <div>
              <Label htmlFor="appointment-duration">Duration (minutes)</Label>
              <input
                id="appointment-duration"
                type="number"
                min={5}
                step={5}
                value={duration}
                onChange={(e) => setDuration(e.target.value)}
                className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              />
            </div>
            <div>
              <Label htmlFor="appointment-notes">Notes</Label>
              <Textarea
                id="appointment-notes"
                rows={3}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="What's this appointment about…"
              />
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={createAppointment.isPending}>
              Book appointment
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
