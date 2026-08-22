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
import { useCreateTemplate } from "@/lib/hooks";

const templateSchema = z.object({
  name: z
    .string()
    .trim()
    .regex(/^[a-z0-9_]+$/, "Lowercase letters, numbers, and underscores only"),
  language: z.string().trim().min(1, "Language code is required"),
  bodyPreview: z.string().trim().max(2000, "Body preview is too long").optional(),
});

type TemplateFieldErrors = Partial<Record<"name" | "language" | "bodyPreview", string>>;

export function NewTemplateDialog() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [language, setLanguage] = useState("en_US");
  const [category, setCategory] = useState("UTILITY");
  const [bodyPreview, setBodyPreview] = useState("");
  const [paramLabels, setParamLabels] = useState<string[]>([]);
  const [fieldErrors, setFieldErrors] = useState<TemplateFieldErrors>({});
  const createTemplate = useCreateTemplate();
  const { toast } = useToast();

  function reset() {
    setName("");
    setLanguage("en_US");
    setCategory("UTILITY");
    setBodyPreview("");
    setParamLabels([]);
    setFieldErrors({});
  }

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
    setFieldErrors({});
    createTemplate.mutate(
      {
        name,
        language,
        category: category || undefined,
        param_labels: paramLabels.filter((label) => label.trim().length > 0),
        body_preview: bodyPreview || undefined,
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
