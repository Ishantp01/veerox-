"use client";

import { useState } from "react";
import { Receipt } from "lucide-react";
import {
  Badge,
  Button,
  Dialog,
  DialogTrigger,
  DialogContent,
  DialogTitle,
  DialogBody,
  DialogFooter,
  EmptyState,
  SkeletonRows,
  Table,
  TableCell,
  TableHeader,
  TableRow,
} from "@/components/ui";
import { useOrgPayments } from "@/lib/hooks/useAdminOrgs";

const STATUS_BADGE: Record<string, "success" | "danger" | "neutral"> = {
  paid: "success",
  failed: "danger",
  created: "neutral",
};

// Raw BillingPayment.status values, mapped to wording that reads as a
// payment outcome rather than an internal order-lifecycle term.
const STATUS_LABEL: Record<string, string> = {
  paid: "Successful",
  failed: "Failed",
  created: "Pending",
};

function formatRupees(cents: number): string {
  return `₹${(cents / 100).toLocaleString("en-IN")}`;
}

export function PaymentHistoryDialog({ orgId, orgName }: { orgId: string; orgName: string }) {
  const [open, setOpen] = useState(false);
  const { data, isLoading, isError } = useOrgPayments(orgId, open);
  const payments = data ?? [];

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger>
        <Button variant="ghost" size="sm" aria-label={`Payment history for ${orgName}`}>
          <Receipt size={14} />
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogTitle>Payment history — {orgName}</DialogTitle>
        <DialogBody>
          {isLoading ? (
            <Table>
              <tbody>
                <SkeletonRows rows={3} cols={4} />
              </tbody>
            </Table>
          ) : isError ? (
            <p className="text-red-600 dark:text-red-400">Could not load payment history.</p>
          ) : payments.length === 0 ? (
            <EmptyState icon={Receipt} title="No payments yet" description="This org hasn't made any payments." />
          ) : (
            <div className="max-h-96 overflow-y-auto rounded-xl border border-slate-200/80 dark:border-slate-800">
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
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
