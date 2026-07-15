import Link from "next/link";
import { Badge, Table, TableCell, TableHeader, TableRow } from "@/components/ui";
import { CampaignStatusBadge } from "@/components/campaigns/campaign-status-badge";
import { formatPercent } from "@/lib/format";
import type { ReportsCampaignRow } from "@/lib/types";

/** Per-campaign conversion table — reuses the existing channel/status badge
 * language from the campaigns list so a "voice" or "whatsapp" tag reads the
 * same way everywhere in the app. */
export function CampaignsConversionTable({ rows }: { rows: ReportsCampaignRow[] }) {
  return (
    <div className="overflow-x-auto rounded-2xl border border-slate-200 bg-white shadow-sm dark:border-slate-800 dark:bg-slate-900">
      <Table>
        <thead>
          <TableRow isHeader>
            <TableHeader>Campaign</TableHeader>
            <TableHeader>Channel</TableHeader>
            <TableHeader>Status</TableHeader>
            <TableHeader>Completed</TableHeader>
            <TableHeader>Qualified</TableHeader>
            <TableHeader>Qualification rate</TableHeader>
          </TableRow>
        </thead>
        <tbody>
          {rows.map((row) => {
            const href = row.channel === "voice" ? `/calling/campaigns/${row.id}` : undefined;
            const nameCell = (
              <span className="font-semibold text-slate-800 dark:text-slate-100">{row.name}</span>
            );
            return (
              <TableRow key={row.id}>
                <TableCell>{href ? <Link href={href}>{nameCell}</Link> : nameCell}</TableCell>
                <TableCell>
                  <Badge variant={row.channel === "voice" ? "voice" : "whatsapp"}>
                    {row.channel === "voice" ? "Voice" : "WhatsApp"}
                  </Badge>
                </TableCell>
                <TableCell>
                  <CampaignStatusBadge status={row.status} />
                </TableCell>
                <TableCell className="tabular-nums">{row.counts.completed}</TableCell>
                <TableCell className="tabular-nums font-semibold text-emerald-600 dark:text-emerald-400">
                  {row.counts.qualified}
                </TableCell>
                <TableCell className="tabular-nums">{formatPercent(row.qualification_rate)}</TableCell>
              </TableRow>
            );
          })}
        </tbody>
      </Table>
    </div>
  );
}
