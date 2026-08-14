"use client";

import { useState } from "react";
import { CalendarPlus } from "lucide-react";
import { z } from "zod";
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

const appointmentSchema = z.object({
  scheduledAt: z
    .string()
    .trim()
    .min(1, "Pick a future date and time")
    .refine((v) => {
      const d = new Date(v);
      return !Number.isNaN(d.getTime()) && d.getTime() > Date.now();
    }, "Pick a future date and time"),
  duration: z.coerce.number().int().min(5).max(480),
  notes: z.string().trim().max(2000, "Notes must be under 2000 characters").optional(),
});

type AppointmentFieldErrors = Partial<Record<"scheduledAt" | "duration" | "notes", string>>;

export function NewAppointmentDialog() {
  const [open, setOpen] = useState(false);
  const [contact, setContact] = useState<Contact | null>(null);
  const [scheduledAt, setScheduledAt] = useState("");
  const [duration, setDuration] = useState("30");
  const [notes, setNotes] = useState("");
  const [fieldErrors, setFieldErrors] = useState<AppointmentFieldErrors>({});
  const createAppointment = useCreateAppointment();
  const { toast } = useToast();

  function reset() {
    setContact(null);
    setScheduledAt("");
    setDuration("30");
    setNotes("");
    setFieldErrors({});
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const result = appointmentSchema.safeParse({ scheduledAt, duration, notes });
    if (!result.success) {
      const errors: AppointmentFieldErrors = {};
      for (const issue of result.error.issues) {
        const key = issue.path[0] as keyof AppointmentFieldErrors;
        if (!errors[key]) errors[key] = issue.message;
      }
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});

    createAppointment.mutate(
      {
        contact_id: contact?.id ?? null,
        scheduled_at: new Date(scheduledAt).toISOString(),
        duration_minutes: result.data.duration,
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
        <form onSubmit={handleSubmit} noValidate>
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
                aria-invalid={fieldErrors.scheduledAt ? true : undefined}
                aria-describedby={fieldErrors.scheduledAt ? "appointment-time-error" : undefined}
                className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              />
              {fieldErrors.scheduledAt && (
                <p id="appointment-time-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.scheduledAt}
                </p>
              )}
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
                aria-invalid={fieldErrors.duration ? true : undefined}
                aria-describedby={fieldErrors.duration ? "appointment-duration-error" : undefined}
                className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              />
              {fieldErrors.duration && (
                <p id="appointment-duration-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.duration}
                </p>
              )}
            </div>
            <div>
              <Label htmlFor="appointment-notes">Notes</Label>
              <Textarea
                id="appointment-notes"
                rows={3}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="What's this appointment about…"
                aria-invalid={fieldErrors.notes ? true : undefined}
                aria-describedby={fieldErrors.notes ? "appointment-notes-error" : undefined}
              />
              {fieldErrors.notes && (
                <p id="appointment-notes-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.notes}
                </p>
              )}
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
