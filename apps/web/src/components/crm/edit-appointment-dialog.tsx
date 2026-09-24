"use client";

import { useEffect, useState } from "react";
import { z } from "zod";
import {
  Button,
  Dialog,
  DialogContent,
  DialogTitle,
  DialogBody,
  DialogFooter,
  Input,
  Label,
  Textarea,
  useToast,
} from "@/components/ui";
import { useUpdateAppointment } from "@/lib/hooks";
import type { Appointment } from "@/lib/types";

const editAppointmentSchema = z.object({
  scheduledAt: z
    .string()
    .trim()
    .min(1, "Pick a date and time"),
  duration: z.coerce.number().int().min(5).max(480),
  notes: z.string().trim().max(2000, "Notes must be under 2000 characters").optional(),
  callbackAt: z.string().trim().optional(),
});

type EditAppointmentFieldErrors = Partial<
  Record<"scheduledAt" | "duration" | "notes" | "callbackAt", string>
>;

/** datetime-local input needs "YYYY-MM-DDTHH:mm" in local time, not the ISO string from the API. */
function toLocalInputValue(iso: string): string {
  const d = new Date(iso);
  const offsetMs = d.getTimezoneOffset() * 60_000;
  return new Date(d.getTime() - offsetMs).toISOString().slice(0, 16);
}

interface EditAppointmentDialogProps {
  appointment: Appointment | null;
  onClose: () => void;
}

export function EditAppointmentDialog({ appointment, onClose }: EditAppointmentDialogProps) {
  const [scheduledAt, setScheduledAt] = useState("");
  const [duration, setDuration] = useState("30");
  const [notes, setNotes] = useState("");
  const [tagsInput, setTagsInput] = useState("");
  const [callbackAt, setCallbackAt] = useState("");
  const [fieldErrors, setFieldErrors] = useState<EditAppointmentFieldErrors>({});
  const updateAppointment = useUpdateAppointment();
  const { toast } = useToast();

  useEffect(() => {
    if (!appointment) return;
    setScheduledAt(toLocalInputValue(appointment.scheduled_at));
    setDuration(String(appointment.duration_minutes));
    setNotes(appointment.notes ?? "");
    setTagsInput((appointment.tags ?? []).join(", "));
    setCallbackAt(appointment.callback_at ? toLocalInputValue(appointment.callback_at) : "");
    setFieldErrors({});
  }, [appointment]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!appointment) return;
    const result = editAppointmentSchema.safeParse({ scheduledAt, duration, notes, callbackAt });
    if (!result.success) {
      const errors: EditAppointmentFieldErrors = {};
      for (const issue of result.error.issues) {
        const key = issue.path[0] as keyof EditAppointmentFieldErrors;
        if (!errors[key]) errors[key] = issue.message;
      }
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});

    const tags = tagsInput
      .split(",")
      .map((t) => t.trim())
      .filter(Boolean);

    updateAppointment.mutate(
      {
        id: appointment.id,
        scheduled_at: new Date(scheduledAt).toISOString(),
        duration_minutes: result.data.duration,
        notes: notes.trim() ? notes.trim() : null,
        tags: tags.length > 0 ? tags : null,
        callback_at: callbackAt ? new Date(callbackAt).toISOString() : null,
      },
      {
        onSuccess: () => {
          toast({ title: "Appointment updated", variant: "success" });
          onClose();
        },
        onError: (err) =>
          toast({ title: "Could not update appointment", description: err.message, variant: "error" }),
      },
    );
  }

  return (
    <Dialog open={appointment !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogTitle>Edit appointment</DialogTitle>
        <form onSubmit={handleSubmit} noValidate>
          <DialogBody className="flex flex-col gap-4">
            <div>
              <Label htmlFor="edit-appointment-time">Date &amp; time *</Label>
              <input
                id="edit-appointment-time"
                type="datetime-local"
                required
                value={scheduledAt}
                onChange={(e) => setScheduledAt(e.target.value)}
                aria-invalid={fieldErrors.scheduledAt ? true : undefined}
                aria-describedby={fieldErrors.scheduledAt ? "edit-appointment-time-error" : undefined}
                className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              />
              {fieldErrors.scheduledAt && (
                <p id="edit-appointment-time-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.scheduledAt}
                </p>
              )}
            </div>
            <div>
              <Label htmlFor="edit-appointment-duration">Duration (minutes)</Label>
              <input
                id="edit-appointment-duration"
                type="number"
                min={5}
                step={5}
                value={duration}
                onChange={(e) => setDuration(e.target.value)}
                aria-invalid={fieldErrors.duration ? true : undefined}
                aria-describedby={fieldErrors.duration ? "edit-appointment-duration-error" : undefined}
                className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              />
              {fieldErrors.duration && (
                <p id="edit-appointment-duration-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.duration}
                </p>
              )}
            </div>
            <div>
              <Label htmlFor="edit-appointment-callback">Call back at</Label>
              <input
                id="edit-appointment-callback"
                type="datetime-local"
                value={callbackAt}
                onChange={(e) => setCallbackAt(e.target.value)}
                className="w-full rounded-xl border border-slate-300 bg-white px-3.5 py-2.5 text-sm text-slate-800 shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary-500 focus-visible:ring-offset-1 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100"
              />
              <p className="mt-1 text-xs text-slate-400 dark:text-slate-600">
                Set if the caller asked to be called back at a specific time. Leave blank to clear.
              </p>
            </div>
            <div>
              <Label htmlFor="edit-appointment-notes">Notes</Label>
              <Textarea
                id="edit-appointment-notes"
                rows={3}
                value={notes}
                onChange={(e) => setNotes(e.target.value)}
                placeholder="What's this appointment about…"
                aria-invalid={fieldErrors.notes ? true : undefined}
                aria-describedby={fieldErrors.notes ? "edit-appointment-notes-error" : undefined}
              />
              {fieldErrors.notes && (
                <p id="edit-appointment-notes-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.notes}
                </p>
              )}
            </div>
            <div>
              <Label htmlFor="edit-appointment-tags">Tags</Label>
              <Input
                id="edit-appointment-tags"
                value={tagsInput}
                onChange={(e) => setTagsInput(e.target.value)}
                placeholder="hot, enterprise, needs-demo"
              />
              <p className="mt-1 text-xs text-slate-400 dark:text-slate-600">
                Comma-separated.
              </p>
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={updateAppointment.isPending}>
              Save changes
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
