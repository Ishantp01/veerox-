"use client";

import { useState } from "react";
import { useFieldArray, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { CheckCircle2, MessageSquare, Plus, Trash2 } from "lucide-react";

import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Label,
  Select,
  Textarea,
  useToast,
} from "@/components/ui";
import { cn } from "@/lib/utils";
import { useOutboundWhatsApp, useTemplates } from "@/lib/hooks";
import { ContactPicker } from "@/components/crm/contact-picker";
import type { Contact } from "@/lib/types";

const whatsappSchema = z
  .object({
    phone: z
      .string()
      .trim()
      .regex(/^\+\d{8,15}$/, "Enter a valid E.164 number, e.g. +919876543210"),
    mode: z.enum(["text", "template"]),
    text: z.string().trim().optional(),
    templateName: z.string().trim().optional(),
    templateLang: z.string().trim().optional(),
    templateParams: z.array(z.object({ value: z.string() })),
  })
  .superRefine((data, ctx) => {
    if (data.mode === "text" && !data.text) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Message body is required.",
        path: ["text"],
      });
    }
    if (data.mode === "template" && !data.templateName) {
      ctx.addIssue({
        code: z.ZodIssueCode.custom,
        message: "Template name is required.",
        path: ["templateName"],
      });
    }
  });

type WhatsAppForm = z.infer<typeof whatsappSchema>;

export interface OutboundWhatsAppFormProps {
  /** Pre-fills the phone field, e.g. when arriving from a user detail page. */
  defaultPhone?: string;
}

/**
 * Outbound WhatsApp send form (POST /admin/outbound/whatsapp). Shared by the
 * per-user detail page and the standalone /whatsapp/send page.
 *
 * Supports two modes: free-form text (only deliverable inside Meta's 24-hour
 * customer-service window) and approved templates (the only way to reach a
 * user outside that window).
 */
export function OutboundWhatsAppForm({ defaultPhone = "" }: OutboundWhatsAppFormProps) {
  const { toast } = useToast();
  const [lastMessageId, setLastMessageId] = useState<string | null>(null);
  const [selectedContact, setSelectedContact] = useState<Contact | null>(null);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [paramLabels, setParamLabels] = useState<string[]>([]);
  const [manualEntry, setManualEntry] = useState(false);
  const outboundWhatsApp = useOutboundWhatsApp();
  const templates = useTemplates({ active: true });

  const {
    register,
    handleSubmit,
    reset,
    getValues,
    setValue,
    watch,
    control,
    formState: { errors },
  } = useForm<WhatsAppForm>({
    resolver: zodResolver(whatsappSchema),
    mode: "onChange",
    defaultValues: {
      phone: defaultPhone || "+91",
      mode: "text",
      text: "",
      templateName: "",
      templateLang: "en_US",
      templateParams: [],
    },
  });

  const { fields, append, remove, replace } = useFieldArray({ control, name: "templateParams" });
  const mode = watch("mode");

  function handleTemplateSelect(templateId: string) {
    setSelectedTemplateId(templateId);
    const template = (templates.data ?? []).find((t) => t.id === templateId);
    if (!template) return;
    setValue("templateName", template.name, { shouldValidate: true });
    setValue("templateLang", template.language);
    setParamLabels(template.param_labels);
    replace(template.param_labels.map(() => ({ value: "" })));
  }

  function handleManualEntryToggle(next: boolean) {
    setManualEntry(next);
    setSelectedTemplateId("");
    setParamLabels([]);
    setValue("templateName", "");
    setValue("templateLang", "en_US");
    replace([]);
  }

  const onSubmit = handleSubmit((values) => {
    setLastMessageId(null);
    outboundWhatsApp.mutate(
      values.mode === "template"
        ? {
            phone: values.phone,
            template_name: values.templateName,
            template_lang: values.templateLang || "en_US",
            template_params: values.templateParams
              .map((p) => p.value)
              .filter((v) => v.length > 0),
          }
        : { phone: values.phone, text: values.text },
      {
        onSuccess: (res) => {
          setLastMessageId(res.wa_message_id);
          // Clear the message fields but keep the phone + mode (and selected
          // template) for follow-ups.
          reset({
            phone: getValues("phone"),
            mode: values.mode,
            text: "",
            templateName: values.mode === "template" ? values.templateName : "",
            templateLang: values.templateLang || "en_US",
            templateParams: paramLabels.map(() => ({ value: "" })),
          });
          toast({
            title: "Message sent",
            description: res.wa_message_id
              ? `Meta id ${res.wa_message_id}`
              : "Queued (no Meta id in local dev).",
            variant: "success",
          });
        },
        onError: (err) => {
          const status = (err as Error & { status?: number }).status;
          toast({
            title: status === 402 ? "Credit limit reached" : "Send failed",
            description:
              status === 402
                ? "You've used up your plan's WhatsApp messages this month. Upgrade your plan to keep sending."
                : err.message,
            variant: "error",
          });
        },
      },
    );
  });

  return (
    <Card className="max-w-xl">
      <CardHeader>
        <div className="flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-emerald-400 to-emerald-600 text-white shadow-sm">
            <MessageSquare size={16} aria-hidden />
          </div>
          <div>
            <CardTitle>Send WhatsApp Message</CardTitle>
            <p className="text-xs text-slate-400 dark:text-slate-500">
              Outbound message attributed to the admin token.
            </p>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        <form onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
          <div>
            <Label htmlFor="contact-picker">Contact</Label>
            <ContactPicker
              value={selectedContact}
              onChange={(contact) => {
                setSelectedContact(contact);
                if (contact) setValue("phone", contact.phone, { shouldValidate: true });
              }}
              placeholder="Search an existing contact, or type a number below…"
            />
          </div>

          <div>
            <Label htmlFor="phone" required>
              Phone (E.164)
            </Label>
            <Input
              id="phone"
              type="tel"
              inputMode="tel"
              autoComplete="tel"
              placeholder="+919876543210"
              maxLength={16}
              className="font-mono"
              aria-invalid={errors.phone ? true : undefined}
              aria-describedby={errors.phone ? "phone-error" : undefined}
              {...register("phone")}
            />
            {errors.phone && (
              <p id="phone-error" className="mt-1.5 text-xs text-red-600">
                {errors.phone.message}
              </p>
            )}
          </div>

          <div>
            <Label>Mode</Label>
            <div
              role="radiogroup"
              aria-label="Message mode"
              className="inline-flex rounded-xl border border-slate-300 bg-slate-100 p-1 dark:border-slate-700 dark:bg-slate-800"
            >
              {(["text", "template"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  role="radio"
                  aria-checked={mode === m}
                  onClick={() => setValue("mode", m)}
                  className={cn(
                    "rounded-lg px-3.5 py-1.5 text-sm font-medium transition-all",
                    mode === m
                      ? "bg-white text-slate-900 shadow-sm dark:bg-slate-700 dark:text-slate-50"
                      : "text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200",
                  )}
                >
                  {m === "text" ? "Free text" : "Template"}
                </button>
              ))}
            </div>
            <p className="mt-1.5 text-xs text-slate-400 dark:text-slate-500">
              {mode === "text"
                ? "Only deliverable inside Meta's 24-hour customer-service window."
                : "Required to reach a user outside the 24-hour window — the template must already be approved in WhatsApp Manager."}
            </p>
          </div>

          {mode === "text" ? (
            <div>
              <Label htmlFor="text" required>
                Message
              </Label>
              <Textarea
                id="text"
                rows={5}
                placeholder="Type the message to send…"
                aria-invalid={errors.text ? true : undefined}
                aria-describedby={errors.text ? "text-error" : undefined}
                {...register("text")}
              />
              {errors.text && (
                <p id="text-error" className="mt-1.5 text-xs text-red-600">
                  {errors.text.message}
                </p>
              )}
            </div>
          ) : (
            <>
              {(() => {
                const hasTemplates = (templates.data ?? []).length > 0;
                const useDropdown = hasTemplates && !manualEntry;
                return useDropdown ? (
                  <div>
                    <div className="mb-1.5 flex items-center justify-between">
                      <Label htmlFor="templateSelect" required className="mb-0">
                        Template
                      </Label>
                      <button
                        type="button"
                        className="text-xs font-medium text-primary-600 hover:underline dark:text-primary-400"
                        onClick={() => handleManualEntryToggle(true)}
                      >
                        Enter manually
                      </button>
                    </div>
                    <Select
                      id="templateSelect"
                      value={selectedTemplateId}
                      onChange={(v) => handleTemplateSelect(v)}
                      className="w-full"
                    >
                      <option value="">Select a template…</option>
                      {(templates.data ?? []).map((t) => (
                        <option key={t.id} value={t.id}>
                          {t.name} ({t.language})
                        </option>
                      ))}
                    </Select>
                    {errors.templateName && (
                      <p className="mt-1.5 text-xs text-red-600">{errors.templateName.message}</p>
                    )}
                  </div>
                ) : (
                  <>
                    <div>
                      <div className="mb-1.5 flex items-center justify-between">
                        <Label htmlFor="templateName" required className="mb-0">
                          Template name
                        </Label>
                        {hasTemplates && (
                          <button
                            type="button"
                            className="text-xs font-medium text-primary-600 hover:underline dark:text-primary-400"
                            onClick={() => handleManualEntryToggle(false)}
                          >
                            Choose from saved templates
                          </button>
                        )}
                      </div>
                      <Input
                        id="templateName"
                        placeholder="e.g. order_confirmation"
                        className="font-mono"
                        aria-invalid={errors.templateName ? true : undefined}
                        aria-describedby={errors.templateName ? "templateName-error" : undefined}
                        {...register("templateName")}
                      />
                      {errors.templateName && (
                        <p id="templateName-error" className="mt-1.5 text-xs text-red-600">
                          {errors.templateName.message}
                        </p>
                      )}
                    </div>

                    <div>
                      <Label htmlFor="templateLang">Language code</Label>
                      <Input
                        id="templateLang"
                        placeholder="en_US"
                        className="font-mono"
                        {...register("templateLang")}
                      />
                    </div>
                  </>
                );
              })()}

              <div>
                <div className="mb-1.5 flex items-center justify-between">
                  <Label className="mb-0">Body parameters</Label>
                  {manualEntry || (templates.data ?? []).length === 0 ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={() => append({ value: "" })}
                    >
                      <Plus size={13} aria-hidden /> Add {`{{${fields.length + 1}}}`}
                    </Button>
                  ) : null}
                </div>
                {fields.length === 0 ? (
                  <p className="text-xs text-slate-400 dark:text-slate-500">
                    {selectedTemplateId
                      ? "This template has no body variables."
                      : "No variables — add one for each {{1}}, {{2}}, ... placeholder in the template body, in order."}
                  </p>
                ) : (
                  <div className="flex flex-col gap-2">
                    {fields.map((field, index) => (
                      <div key={field.id} className="flex items-center gap-2">
                        <span className="w-9 shrink-0 font-mono text-xs text-slate-400">
                          {`{{${index + 1}}}`}
                        </span>
                        <Input
                          placeholder={paramLabels[index] || `Value for {{${index + 1}}}`}
                          {...register(`templateParams.${index}.value` as const)}
                        />
                        {(manualEntry || (templates.data ?? []).length === 0) && (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            aria-label={`Remove parameter ${index + 1}`}
                            onClick={() => remove(index)}
                          >
                            <Trash2 size={14} aria-hidden />
                          </Button>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}

          {lastMessageId !== null && (
            <div
              role="status"
              className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-400"
            >
              <p className="mb-1 flex items-center gap-1.5 font-bold">
                <CheckCircle2 size={14} aria-hidden /> Message sent
              </p>
              <p className="break-all font-mono text-xs text-emerald-600">
                wa_message_id: {lastMessageId}
              </p>
            </div>
          )}

          <Button type="submit" variant="primary" loading={outboundWhatsApp.isPending}>
            {!outboundWhatsApp.isPending && <MessageSquare size={15} aria-hidden />}
            {outboundWhatsApp.isPending ? "Sending…" : "Send Message"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
