"use client";

import { useMemo, useState } from "react";
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
  Select,
  useToast,
} from "@/components/ui";
import { CallAnalyticsPanel } from "@/components/calling/call-analytics-panel";
import { useOrgCountryCode, useOrgNumbers, useOutboundCall } from "@/lib/hooks";
import { PHONE_MESSAGE, isValidPhone, normalizePhone } from "@/lib/phone";

const PROVIDER_LABEL: Record<"plivo" | "twilio", string> = {
  plivo: "Plivo",
  twilio: "Twilio",
};

type DialForm = { to_phone: string };

export default function CallingDialPage() {
  const { toast } = useToast();
  const [callSid, setCallSid] = useState<string | null>(null);
  const outboundCall = useOutboundCall();
  const { data: orgNumbers } = useOrgNumbers();
  const countryCode = useOrgCountryCode();
  // A number typed without a country code gets the org's default one.
  const dialSchema = useMemo(
    () =>
      z.object({
        to_phone: z
          .string()
          .trim()
          .refine((v) => isValidPhone(v, countryCode), PHONE_MESSAGE),
      }),
    [countryCode],
  );

  // Only worth letting someone choose when the org actually has a dedicated
  // number on both providers — otherwise there's nothing to pick between,
  // the single configured (or platform default) number is just used.
  const hasBothProviders =
    Boolean(orgNumbers?.phone_numbers.some((n) => n.provider === "plivo")) &&
    Boolean(orgNumbers?.phone_numbers.some((n) => n.provider === "twilio"));
  const [provider, setProvider] = useState<"plivo" | "twilio">("plivo");

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<DialForm>({
    resolver: zodResolver(dialSchema),
    defaultValues: { to_phone: countryCode },
    mode: "onChange",
  });

  const onSubmit = handleSubmit((values) => {
    setCallSid(null);
    outboundCall.mutate(
      { to_phone: normalizePhone(values.to_phone, countryCode), provider: hasBothProviders ? provider : undefined },
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
          const status = (err as Error & { status?: number }).status;
          toast({
            title: status === 402 ? "Credit limit reached" : "Call failed",
            description:
              status === 402
                ? "You've used up your plan's call minutes this month. Upgrade your plan to keep calling."
                : err.message,
            variant: "error",
          });
        },
      },
    );
  });

  return (
    <div className="mx-auto max-w-7xl">
      <PageHeader
        title="AI Calling Agent"
        description="Place an outbound call — the AI agent answers when the recipient picks up."
      />

      <div className="flex flex-col gap-6 lg:flex-row lg:items-start">
        <Card className="w-full max-w-md">
          <CardHeader>
            <div className="flex items-center gap-3">
              <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-primary-400 to-primary-600 text-white shadow-glow">
                <Phone size={16} aria-hidden />
              </div>
              <div>
                <CardTitle>Outbound Call</CardTitle>
                <p className="text-xs text-slate-400 dark:text-slate-500">
                  via {hasBothProviders ? PROVIDER_LABEL[provider] : "Plivo"} + AI agent
                </p>
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
                  placeholder={`${countryCode}9876543210`}
                  autoComplete="tel"
                  maxLength={16}
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
                    Country code is optional — {countryCode} is added automatically.
                  </p>
                )}
              </div>

              {hasBothProviders && (
                <div>
                  <Label htmlFor="call_provider">Call from</Label>
                  <Select
                    id="call_provider"
                    value={provider}
                    onChange={(value) => setProvider(value as "plivo" | "twilio")}
                  >
                    <option value="plivo">Plivo</option>
                    <option value="twilio">Twilio</option>
                  </Select>
                  <p className="mt-1.5 text-xs text-slate-400">
                    Your org has a dedicated number on both — pick which one places this call.
                  </p>
                </div>
              )}

              {callSid && (
                <div
                  role="status"
                  className="rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700 dark:border-emerald-500/20 dark:bg-emerald-500/10 dark:text-emerald-400"
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

          <div className="mx-6 mb-6 rounded-2xl border border-primary-100 bg-gradient-to-br from-primary-50 to-white px-4 py-3 dark:border-primary-500/15 dark:from-primary-500/10 dark:to-transparent">
            <p className="mb-1 flex items-center gap-1.5 text-xs font-semibold text-primary-600 dark:text-primary-400">
              <Info size={12} aria-hidden /> How it works
            </p>
            <p className="text-xs text-primary-500 dark:text-primary-400/80">
              {hasBothProviders ? PROVIDER_LABEL[provider] : "Plivo"} calls the recipient → AI
              agent joins → conversation is logged automatically.
            </p>
          </div>
        </Card>

        <CallAnalyticsPanel />
      </div>
    </div>
  );
}
