"use client";

import { Receipt } from "lucide-react";
import {
  Badge,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
} from "@/components/ui";
import { useBillingPayments } from "@/lib/hooks/useBilling";

const STATUS_BADGE: Record<string, "success" | "danger" | "neutral"> = {
  paid: "success",
  failed: "danger",
  created: "neutral",
};

// Raw BillingPayment.status values, mapped to wording a customer recognizes
// as a payment outcome rather than an internal order-lifecycle term.
const STATUS_LABEL: Record<string, string> = {
  paid: "Successful",
  failed: "Failed",
  created: "Pending",
};

function formatRupees(cents: number): string {
  return `₹${(cents / 100).toLocaleString("en-IN")}`;
}

/** Self-service billing/payment history for the org's own Billing page. */
export function BillingHistoryCard() {
  const { data, isLoading, isError } = useBillingPayments();
  const payments = data ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2.5">
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-primary-400 to-primary-600 text-white shadow-glow">
            <Receipt size={15} aria-hidden />
          </span>
          Billing history
        </CardTitle>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Table>
            <tbody>
              <SkeletonRows rows={3} cols={4} />
            </tbody>
          </Table>
        ) : isError ? (
          <p className="text-sm text-red-600 dark:text-red-400">Could not load billing history.</p>
        ) : payments.length === 0 ? (
          <EmptyState
            icon={Receipt}
            title="No payments yet"
            description="Your payment history will appear here once you recharge a plan."
          />
        ) : (
          <div className="overflow-x-auto rounded-xl border border-slate-200/80 dark:border-slate-800">
            <Table>
              <thead>
                <TableRow isHeader>
                  <TableHeader>Date</TableHeader>
                  <TableHeader>Plan</TableHeader>
                  <TableHeader>Amount</TableHeader>
                  <TableHeader>Status</TableHeader>
                </TableRow>
              </thead>
              <tbody>
                {payments.map((payment) => (
                  <TableRow key={payment.id}>
                    <TableCell>{new Date(payment.created_at).toLocaleDateString()}</TableCell>
                    <TableCell>{payment.plan_name ?? "—"}</TableCell>
                    <TableCell>{formatRupees(payment.amount_cents)}</TableCell>
                    <TableCell>
                      <Badge variant={STATUS_BADGE[payment.status] ?? "neutral"}>
                        {STATUS_LABEL[payment.status] ?? payment.status}
                      </Badge>
                    </TableCell>
                  </TableRow>
                ))}
              </tbody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
