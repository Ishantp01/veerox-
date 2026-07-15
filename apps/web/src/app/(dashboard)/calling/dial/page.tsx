"use client";

import { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { CheckCircle2, Info, Phone } from "lucide-react";

import { PageHeader } from "@/components/layout/page-header";
import {
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  Input,
  Label,
  useToast,
} from "@/components/ui";
import { useOutboundCall } from "@/lib/hooks";

const dialSchema = z.object({
  // E.164-ish: a leading "+" followed by 8–15 digits.
  to_phone: z
    .string()
    .trim()
    .regex(/^\+\d{8,15}$/, "Enter a valid E.164 number, e.g. +919876543210"),
});

type DialForm = z.infer<typeof dialSchema>;

export default function DialPage() {
  const { toast } = useToast();
  const [callSid, setCallSid] = useState<string | null>(null);
  const outboundCall = useOutboundCall();

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<DialForm>({
    resolver: zodResolver(dialSchema),
    defaultValues: { to_phone: "" },
  });

  const onSubmit = handleSubmit((values) => {
    setCallSid(null);
    outboundCall.mutate(
      { to_phone: values.to_phone },
      {
        onSuccess: (res) => {
          setCallSid(res.call_sid);
          toast({
            title: "Call initiated",
            description: `SID ${res.call_sid}`,
            variant: "success",
          });
        },
        onError: (err) => {
          toast({
            title: "Call failed",
            description: err.message,
            variant: "error",
          });
        },
      },
    );
  });

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="Dial"
        description="Place an outbound call — the AI agent answers when the recipient picks up."
      />

      <div className="max-w-md">
        <Card>
          <CardHeader>
            <div className="flex items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-indigo-600 text-white shadow-sm shadow-indigo-200">
                <Phone size={16} aria-hidden />
              </div>
              <div>
                <CardTitle>Outbound Call</CardTitle>
                <p className="text-xs text-slate-400">via Plivo + AI agent</p>
              </div>
            </div>
          </CardHeader>
          <CardContent>
            <form onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
              <div>
                <Label htmlFor="to_phone" required>
                  Phone number (E.164)
                </Label>
                <Input
                  id="to_phone"
                  type="tel"
                  inputMode="tel"
                  placeholder="+919876543210"
                  autoComplete="tel"
                  className="font-mono"
                  aria-invalid={errors.to_phone ? true : undefined}
                  aria-describedby={
                    errors.to_phone ? "to_phone-error" : "to_phone-hint"
                  }
                  {...register("to_phone")}
                />
                {errors.to_phone ? (
                  <p id="to_phone-error" className="mt-1.5 text-xs text-red-600">
                    {errors.to_phone.message}
                  </p>
                ) : (
                  <p id="to_phone-hint" className="mt-1.5 text-xs text-slate-400">
                    Include the country code, e.g. +91 for India.
                  </p>
                )}
              </div>

              {callSid && (
                <div
                  role="status"
                  className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700"
                >
                  <p className="mb-1 flex items-center gap-1.5 font-bold">
                    <CheckCircle2 size={14} aria-hidden /> Call initiated
                  </p>
                  <p className="break-all font-mono text-xs text-emerald-600">
                    SID: {callSid}
                  </p>
                </div>
              )}

              <Button
                type="submit"
                variant="primary"
                loading={outboundCall.isPending}
                className="w-full"
              >
                {!outboundCall.isPending && <Phone size={15} aria-hidden />}
                {outboundCall.isPending ? "Dialing…" : "Dial Now"}
              </Button>
            </form>
          </CardContent>
        </Card>

        <div className="mt-4 rounded-2xl border border-indigo-100 bg-indigo-50 px-4 py-3">
          <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-indigo-600">
            <Info size={12} aria-hidden /> How it works
          </p>
          <p className="text-xs text-indigo-500">
            Plivo calls the recipient → AI agent joins → conversation is logged
            automatically.
          </p>
        </div>
      </div>
    </div>
  );
}
