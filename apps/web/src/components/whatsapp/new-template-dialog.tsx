"use client";

import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
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
  Textarea,
  useToast,
} from "@/components/ui";
import { useCreateTemplate, useWhatsappAssets } from "@/lib/hooks";
import type { TemplateButton } from "@/lib/types";

const MEDIA_HEADER_TYPES = ["IMAGE", "VIDEO", "DOCUMENT"] as const;
type HeaderKind = "TEXT" | (typeof MEDIA_HEADER_TYPES)[number];

const templateSchema = z.object({
  name: z
    .string()
    .trim()
    .regex(/^[a-z0-9_]+$/, "Lowercase letters, numbers, and underscores only"),
  language: z.string().trim().min(1, "Language code is required"),
  bodyPreview: z.string().trim().max(2000, "Body preview is too long").optional(),
});

type TemplateFieldErrors = Partial<Record<"name" | "language" | "bodyPreview", string>>;

const BUTTON_TYPES: { value: TemplateButton["type"]; label: string }[] = [
  { value: "QUICK_REPLY", label: "Quick reply" },
  { value: "URL", label: "Website URL" },
  { value: "PHONE_NUMBER", label: "Call phone number" },
  { value: "COPY_CODE", label: "Copy offer code" },
];

function emptyButton(type: TemplateButton["type"]): TemplateButton {
  return { type, text: "", url: "", phone_number: "", example: "" };
}

export function NewTemplateDialog() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [language, setLanguage] = useState("en_US");
  const [category, setCategory] = useState("UTILITY");
  const [bodyPreview, setBodyPreview] = useState("");
  const [paramLabels, setParamLabels] = useState<string[]>([]);
  const [hasHeader, setHasHeader] = useState(false);
  const [headerKind, setHeaderKind] = useState<HeaderKind>("TEXT");
  const [headerText, setHeaderText] = useState("");
  const [headerExample, setHeaderExample] = useState("");
  const [headerAssetId, setHeaderAssetId] = useState("");
  const [footerText, setFooterText] = useState("");
  const [buttons, setButtons] = useState<TemplateButton[]>([]);
  const [fieldErrors, setFieldErrors] = useState<TemplateFieldErrors>({});
  const createTemplate = useCreateTemplate();
  const assets = useWhatsappAssets();
  const { toast } = useToast();

  const matchingAssets = (assets.data ?? []).filter(
    (a) => a.media_type === headerKind.toLowerCase(),
  );

  function reset() {
    setName("");
    setLanguage("en_US");
    setCategory("UTILITY");
    setBodyPreview("");
    setParamLabels([]);
    setHasHeader(false);
    setHeaderKind("TEXT");
    setHeaderText("");
    setHeaderExample("");
    setHeaderAssetId("");
    setFooterText("");
    setButtons([]);
    setFieldErrors({});
  }

  function updateButton(index: number, patch: Partial<TemplateButton>) {
    setButtons((prev) => prev.map((b, i) => (i === index ? { ...b, ...patch } : b)));
  }

  const isMediaHeader = hasHeader && MEDIA_HEADER_TYPES.includes(headerKind as (typeof MEDIA_HEADER_TYPES)[number]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const parsed = templateSchema.safeParse({ name, language, bodyPreview });
    if (!parsed.success) {
      const errors: TemplateFieldErrors = {};
      for (const issue of parsed.error.issues) {
        const key = issue.path[0] as keyof TemplateFieldErrors;
        if (!errors[key]) errors[key] = issue.message;
      }
      setFieldErrors(errors);
      return;
    }
    if (isMediaHeader && !headerAssetId) {
      toast({
        title: "Pick a header file",
        description: `Choose a saved ${headerKind.toLowerCase()} file from WhatsApp Files for the header.`,
        variant: "error",
      });
      return;
    }
    setFieldErrors({});
    createTemplate.mutate(
      {
        name,
        language,
        category: category || undefined,
        param_labels: paramLabels.filter((label) => label.trim().length > 0),
        body_preview: bodyPreview || undefined,
        header_type: hasHeader
          ? isMediaHeader
            ? headerKind
            : headerText.trim()
              ? "TEXT"
              : undefined
          : undefined,
        header_text: hasHeader && !isMediaHeader && headerText.trim() ? headerText.trim() : undefined,
        header_example:
          hasHeader && !isMediaHeader && headerExample.trim() ? headerExample.trim() : undefined,
        header_asset_id: isMediaHeader ? headerAssetId : undefined,
        footer_text: footerText.trim() || undefined,
        buttons: buttons.filter((b) => b.type === "QUICK_REPLY" || (b.text ?? "").trim()),
      },
      {
        onSuccess: () => {
          toast({
            title: bodyPreview
              ? "Submitted to Meta for review"
              : "Template saved (not submitted to Meta)",
            description: bodyPreview
              ? "Check back for the approval status — usually minutes to 24h."
              : undefined,
            variant: "success",
          });
          reset();
          setOpen(false);
        },
        onError: (err) =>
          toast({ title: "Could not create template", description: err.message, variant: "error" }),
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger>
        <Button variant="primary" size="md">
          <Plus size={15} aria-hidden />
          New Template
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogTitle>New WhatsApp template</DialogTitle>
        <form onSubmit={handleSubmit} noValidate>
          <DialogBody className="flex flex-col gap-4">
            <div className="grid gap-4 sm:grid-cols-2">
              <div>
                <Label htmlFor="template-name" required>
                  Template name
                </Label>
                <Input
                  id="template-name"
                  required
                  className="font-mono"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="order_confirmation"
                  aria-invalid={fieldErrors.name ? true : undefined}
                  aria-describedby={fieldErrors.name ? "template-name-error" : undefined}
                />
                {fieldErrors.name && (
                  <p id="template-name-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.name}
                  </p>
                )}
                <p className="mt-1.5 text-xs text-slate-400 dark:text-slate-500">
                  Filling in a body below submits this to Meta under this exact name — it can&apos;t be
                  changed once submitted.
                </p>
              </div>
              <div>
                <Label htmlFor="template-language" required>
                  Language code
                </Label>
                <Input
                  id="template-language"
                  required
                  className="font-mono"
                  value={language}
                  onChange={(e) => setLanguage(e.target.value)}
                  placeholder="en_US"
                  aria-invalid={fieldErrors.language ? true : undefined}
                  aria-describedby={fieldErrors.language ? "template-language-error" : undefined}
                />
                {fieldErrors.language && (
                  <p id="template-language-error" className="mt-1.5 text-xs text-red-600">
                    {fieldErrors.language}
                  </p>
                )}
              </div>
            </div>
            <div>
              <Label htmlFor="template-category">Category</Label>
              <Select
                id="template-category"
                value={category}
                onChange={setCategory}
                className="w-full"
              >
                <option value="UTILITY">Utility</option>
                <option value="MARKETING">Marketing</option>
                <option value="AUTHENTICATION">Authentication</option>
              </Select>
              <p className="mt-1.5 text-xs text-slate-400 dark:text-slate-500">
                Required by Meta when a body is filled in below — Meta rejects mismatched or
                misleading categories, so pick the closest fit.
              </p>
            </div>

            {/* Header */}
            <div className="rounded-xl border border-slate-200 p-3.5 dark:border-slate-800">
              <label className="flex items-center gap-2 text-sm font-semibold text-slate-700 dark:text-slate-200">
                <input
                  type="checkbox"
                  checked={hasHeader}
                  onChange={(e) => setHasHeader(e.target.checked)}
                  className="rounded border-slate-300 dark:border-slate-600"
                />
                Add a header
              </label>
              {hasHeader && (
                <div className="mt-3 flex flex-col gap-3">
                  <div>
                    <Label htmlFor="template-header-kind">Header type</Label>
                    <Select
                      id="template-header-kind"
                      value={headerKind}
                      onChange={(value) => {
                        setHeaderKind(value as HeaderKind);
                        setHeaderAssetId("");
                      }}
                      className="w-full"
                    >
                      <option value="TEXT">Text</option>
                      <option value="IMAGE">Image</option>
                      <option value="VIDEO">Video</option>
                      <option value="DOCUMENT">Document</option>
                    </Select>
                  </div>

                  {isMediaHeader ? (
                    <div>
                      <Label htmlFor="template-header-asset" required>
                        Header {headerKind.toLowerCase()} file
                      </Label>
                      <Select
                        id="template-header-asset"
                        value={headerAssetId}
                        onChange={setHeaderAssetId}
                        className="w-full"
                      >
                        <option value="">Select a saved file…</option>
                        {matchingAssets.map((a) => (
                          <option key={a.id} value={a.id}>
                            {a.name}
                          </option>
                        ))}
                      </Select>
                      <p className="mt-1.5 text-xs text-slate-400 dark:text-slate-500">
                        {matchingAssets.length === 0
                          ? `No ${headerKind.toLowerCase()} files saved yet — add one under WhatsApp Files first.`
                          : "Uploaded to Meta as this template's header when you create it."}
                      </p>
                    </div>
                  ) : (
                    <>
                      <div>
                        <Label htmlFor="template-header-text">Header text</Label>
                        <Input
                          id="template-header-text"
                          maxLength={60}
                          value={headerText}
                          onChange={(e) => setHeaderText(e.target.value)}
                          placeholder='e.g. "Your order is on its way" or "Hi {{1}}"'
                        />
                      </div>
                      {headerText.includes("{{1}}") && (
                        <div>
                          <Label htmlFor="template-header-example">Example value for {"{{1}}"}</Label>
                          <Input
                            id="template-header-example"
                            value={headerExample}
                            onChange={(e) => setHeaderExample(e.target.value)}
                            placeholder="e.g. Asha"
                          />
                        </div>
                      )}
                    </>
                  )}
                </div>
              )}
            </div>

            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <Label className="mb-0">Body parameters</Label>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => setParamLabels((prev) => [...prev, ""])}
                >
                  <Plus size={13} aria-hidden /> Add {`{{${paramLabels.length + 1}}}`}
                </Button>
              </div>
              {paramLabels.length === 0 ? (
                <p className="text-xs text-slate-400 dark:text-slate-500">
                  No variables — add one for each {`{{1}}, {{2}}, ...`} placeholder in the
                  template body, in order. Meta requires an example value for each (e.g.
                  &quot;Asha&quot;, not a label like &quot;Customer name&quot;) to approve the
                  template — what&apos;s typed here is sent to Meta as that example.
                </p>
              ) : (
                <div className="flex flex-col gap-2">
                  {paramLabels.map((label, index) => (
                    <div key={index} className="flex items-center gap-2">
                      <span className="w-9 shrink-0 font-mono text-xs text-slate-400">
                        {`{{${index + 1}}}`}
                      </span>
                      <Input
                        value={label}
                        onChange={(e) =>
                          setParamLabels((prev) =>
                            prev.map((l, i) => (i === index ? e.target.value : l)),
                          )
                        }
                        placeholder={`Example value for {{${index + 1}}}, e.g. "Asha"`}
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        size="sm"
                        aria-label={`Remove parameter ${index + 1}`}
                        onClick={() => setParamLabels((prev) => prev.filter((_, i) => i !== index))}
                      >
                        <Trash2 size={14} aria-hidden />
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div>
              <Label htmlFor="template-body-preview">Body</Label>
              <Textarea
                id="template-body-preview"
                rows={3}
                value={bodyPreview}
                onChange={(e) => setBodyPreview(e.target.value)}
                placeholder={
                  'Write the template body here, e.g. "Hi {{1}}, your appointment is on {{2}}." ' +
                  "Filling this in submits the template to Meta for review. Leave blank to just " +
                  "save a local record without submitting anything."
                }
                aria-invalid={fieldErrors.bodyPreview ? true : undefined}
                aria-describedby={fieldErrors.bodyPreview ? "template-body-preview-error" : undefined}
              />
              {fieldErrors.bodyPreview && (
                <p id="template-body-preview-error" className="mt-1.5 text-xs text-red-600">
                  {fieldErrors.bodyPreview}
                </p>
              )}
            </div>

            {/* Footer */}
            <div>
              <Label htmlFor="template-footer">Footer (optional)</Label>
              <Input
                id="template-footer"
                maxLength={60}
                value={footerText}
                onChange={(e) => setFooterText(e.target.value)}
                placeholder='e.g. "Veerox AI — reply STOP to opt out"'
              />
              <p className="mt-1.5 text-xs text-slate-400 dark:text-slate-500">
                Small gray text under the body. Static only — no {`{{1}}`} variables allowed here.
              </p>
            </div>

            {/* Buttons */}
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <Label className="mb-0">Buttons (optional)</Label>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  disabled={buttons.length >= 10}
                  onClick={() => setButtons((prev) => [...prev, emptyButton("QUICK_REPLY")])}
                >
                  <Plus size={13} aria-hidden /> Add button
                </Button>
              </div>
              {buttons.length === 0 ? (
                <p className="text-xs text-slate-400 dark:text-slate-500">
                  Quick replies, a website link, a call button, or a copy-code button — shown
                  below the message.
                </p>
              ) : (
                <div className="flex flex-col gap-3">
                  {buttons.map((btn, index) => (
                    <div
                      key={index}
                      className="flex flex-col gap-2 rounded-xl border border-slate-200 p-3 dark:border-slate-800"
                    >
                      <div className="flex items-center gap-2">
                        <Select
                          value={btn.type}
                          onChange={(value) =>
                            updateButton(index, emptyButton(value as TemplateButton["type"]))
                          }
                          className="w-full"
                        >
                          {BUTTON_TYPES.map((t) => (
                            <option key={t.value} value={t.value}>
                              {t.label}
                            </option>
                          ))}
                        </Select>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          aria-label={`Remove button ${index + 1}`}
                          onClick={() => setButtons((prev) => prev.filter((_, i) => i !== index))}
                        >
                          <Trash2 size={14} aria-hidden />
                        </Button>
                      </div>

                      {btn.type === "QUICK_REPLY" && (
                        <Input
                          value={btn.text ?? ""}
                          onChange={(e) => updateButton(index, { text: e.target.value })}
                          placeholder='Button text, e.g. "Yes, confirm"'
                        />
                      )}

                      {btn.type === "URL" && (
                        <>
                          <Input
                            value={btn.text ?? ""}
                            onChange={(e) => updateButton(index, { text: e.target.value })}
                            placeholder='Button text, e.g. "Visit website"'
                          />
                          <Input
                            className="font-mono"
                            value={btn.url ?? ""}
                            onChange={(e) => updateButton(index, { url: e.target.value })}
                            placeholder="https://veerox.ai/orders/{{1}}"
                          />
                          {(btn.url ?? "").includes("{{1}}") && (
                            <Input
                              value={btn.example ?? ""}
                              onChange={(e) => updateButton(index, { example: e.target.value })}
                              placeholder="Example value for {{1}} in the URL"
                            />
                          )}
                        </>
                      )}

                      {btn.type === "PHONE_NUMBER" && (
                        <>
                          <Input
                            value={btn.text ?? ""}
                            onChange={(e) => updateButton(index, { text: e.target.value })}
                            placeholder='Button text, e.g. "Call us"'
                          />
                          <Input
                            className="font-mono"
                            value={btn.phone_number ?? ""}
                            onChange={(e) => updateButton(index, { phone_number: e.target.value })}
                            placeholder="+919999999999"
                          />
                        </>
                      )}

                      {btn.type === "COPY_CODE" && (
                        <Input
                          value={btn.example ?? ""}
                          onChange={(e) => updateButton(index, { example: e.target.value })}
                          placeholder="Sample offer code, e.g. SAVE20"
                        />
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" variant="primary" loading={createTemplate.isPending}>
              Create template
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
