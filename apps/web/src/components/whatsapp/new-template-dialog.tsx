"use client";

import { useState } from "react";
import { Plus, Trash2 } from "lucide-react";
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
  Textarea,
  useToast,
} from "@/components/ui";
import { useCreateTemplate } from "@/lib/hooks";

export function NewTemplateDialog() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [language, setLanguage] = useState("en_US");
  const [category, setCategory] = useState("");
  const [bodyPreview, setBodyPreview] = useState("");
  const [paramLabels, setParamLabels] = useState<string[]>([]);
  const createTemplate = useCreateTemplate();
  const { toast } = useToast();

  function reset() {
    setName("");
    setLanguage("en_US");
    setCategory("");
    setBodyPreview("");
    setParamLabels([]);
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
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
          toast({ title: "Template created", variant: "success" });
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
        <form onSubmit={handleSubmit}>
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
                />
                <p className="mt-1.5 text-xs text-slate-400 dark:text-slate-500">
                  Must exactly match the name approved in WhatsApp Manager.
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
                />
              </div>
            </div>
            <div>
              <Label htmlFor="template-category">Category (optional)</Label>
              <Input
                id="template-category"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                placeholder="marketing / utility / authentication"
              />
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
                  No variables — add one for each {"{{1}}, {{2}}, ..."} placeholder in the
                  template body, in order, with a short label (e.g. "Customer name").
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
                        placeholder={`Label for {{${index + 1}}}`}
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
              <Label htmlFor="template-body-preview">Body preview (optional)</Label>
              <Textarea
                id="template-body-preview"
                rows={3}
                value={bodyPreview}
                onChange={(e) => setBodyPreview(e.target.value)}
                placeholder="Paste the approved template body here for reference."
              />
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
