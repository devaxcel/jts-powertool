"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, Printer } from "lucide-react";
import { fetchInvoice } from "@/lib/api";
import { Invoice } from "@/lib/types";
import { Alert, LoadingState, btn } from "@/components/ui";
import { InvoiceStatusBadge } from "@/components/InvoicesPanel";
import { describeRange, formatUsd } from "@/lib/periods";

const fmtDate = (iso?: string | null) =>
  iso ? new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString(undefined, { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" }) : "—";

export default function InvoicePage() {
  const params = useParams<{ invoiceId: string }>();
  const [invoice, setInvoice] = useState<Invoice | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!params?.invoiceId) return;
    fetchInvoice(params.invoiceId)
      .then(setInvoice)
      .catch((e) => setError(e.message));
  }, [params?.invoiceId]);

  if (error) {
    return (
      <div className="max-w-3xl mx-auto space-y-4">
        <Link href="/billing?view=invoices" className={btn.secondary}>
          <ArrowLeft className="h-3.5 w-3.5" /> Back to invoices
        </Link>
        <Alert type="error">{error}</Alert>
      </div>
    );
  }
  if (!invoice) return <LoadingState label="Loading invoice..." />;

  const markupAmount = invoice.amount_usd - invoice.usage_cost_usd;

  return (
    <div className="max-w-3xl mx-auto space-y-4">
      <div className="flex items-center justify-between print:hidden">
        <Link href="/billing?view=invoices" className={btn.secondary}>
          <ArrowLeft className="h-3.5 w-3.5" /> Back to invoices
        </Link>
        <button onClick={() => window.print()} className={btn.primary}>
          <Printer className="h-3.5 w-3.5" /> Print or save as PDF
        </button>
      </div>

      {invoice.status === "void" && (
        <div className="print:hidden">
          <Alert type="warning" title="This invoice was voided">
            {invoice.void_reason || "It should not be paid."}
          </Alert>
        </div>
      )}

      <article className="bg-white border border-gray-200 rounded-2xl p-8 sm:p-10 shadow-sm print:shadow-none print:border-0 print:p-0 space-y-8 text-gray-800">
        <header className="flex flex-col sm:flex-row sm:items-start justify-between gap-6">
          <div>
            <p className="text-2xl font-bold text-gray-900">JTS PowerTool</p>
            <p className="text-sm text-gray-500">AI assistant usage</p>
          </div>
          <div className="sm:text-right space-y-1">
            <p className="text-xl font-semibold text-gray-900">
              Invoice {invoice.status === "void" && <span className="text-rose-600">(VOID)</span>}
            </p>
            <p className="font-mono text-sm">{invoice.invoice_number}</p>
            <div className="print:hidden">
              <InvoiceStatusBadge status={invoice.display_status} />
            </div>
          </div>
        </header>

        <section className="grid grid-cols-1 sm:grid-cols-2 gap-6 text-sm">
          <div className="space-y-1">
            <p className="text-xs font-semibold uppercase tracking-wider text-gray-500">Bill to</p>
            <p className="font-semibold text-gray-900">{invoice.bill_to?.name || invoice.folder_name}</p>
            {invoice.bill_to?.contact && <p>Attn: {invoice.bill_to.contact}</p>}
            {invoice.bill_to?.address && <p className="whitespace-pre-line">{invoice.bill_to.address}</p>}
            {invoice.bill_to?.email && <p>{invoice.bill_to.email}</p>}
            {invoice.bill_to?.phone && <p>{invoice.bill_to.phone}</p>}
          </div>
          <div className="space-y-1 sm:text-right">
            <p>
              <span className="text-gray-500">Client: </span>
              {invoice.folder_name}
            </p>
            <p>
              <span className="text-gray-500">Period: </span>
              {describeRange({ start: invoice.period_start, end: invoice.period_end })}
            </p>
            <p>
              <span className="text-gray-500">Issued: </span>
              {fmtDate(invoice.created_at)}
            </p>
            <p>
              <span className="text-gray-500">Due: </span>
              {fmtDate(invoice.due_date)}
            </p>
          </div>
        </section>

        <section>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b-2 border-gray-200 text-left text-xs uppercase tracking-wider text-gray-500">
                <th className="py-2 font-semibold">Slack channel</th>
                <th className="py-2 font-semibold text-right">AI replies</th>
                <th className="py-2 font-semibold text-right">Tokens</th>
                <th className="py-2 font-semibold text-right">AI cost (USD)</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {invoice.line_items.length === 0 ? (
                <tr>
                  <td colSpan={4} className="py-4 text-center text-gray-500">
                    No billable AI usage in this period.
                  </td>
                </tr>
              ) : (
                invoice.line_items.map((li) => (
                  <tr key={li.channel_id}>
                    <td className="py-2">{li.channel_name}</td>
                    <td className="py-2 text-right tabular-nums">{li.replies.toLocaleString()}</td>
                    <td className="py-2 text-right tabular-nums">{li.total_tokens.toLocaleString()}</td>
                    <td className="py-2 text-right tabular-nums">${li.cost_usd.toFixed(4)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>

          <div className="mt-4 ml-auto w-full sm:w-72 space-y-1 text-sm">
            <div className="flex justify-between">
              <span className="text-gray-500">AI usage cost</span>
              <span className="tabular-nums">{formatUsd(invoice.usage_cost_usd)}</span>
            </div>
            {invoice.markup_percent > 0 && (
              <div className="flex justify-between">
                <span className="text-gray-500">Service fee ({invoice.markup_percent}%)</span>
                <span className="tabular-nums">{formatUsd(markupAmount)}</span>
              </div>
            )}
            <div className="flex justify-between border-t-2 border-gray-200 pt-2 text-base font-semibold text-gray-900">
              <span>Total due</span>
              <span className="tabular-nums">{formatUsd(invoice.amount_usd)}</span>
            </div>
          </div>
        </section>

        {invoice.status === "paid" && (
          <p className="text-sm text-emerald-700 font-medium">
            Paid on {fmtDate(invoice.paid_at)}
            {invoice.payment_reference ? ` · Reference: ${invoice.payment_reference}` : ""}. Thank you.
          </p>
        )}

        {invoice.notes && (
          <section className="text-sm">
            <p className="text-xs font-semibold uppercase tracking-wider text-gray-500 mb-1">Notes</p>
            <p className="whitespace-pre-line">{invoice.notes}</p>
          </section>
        )}

        <footer className="text-xs text-gray-400 border-t border-gray-100 pt-4">
          Usage is measured per AI reply in the client&apos;s Slack channels. Replies made with the client&apos;s own Anthropic key
          are not billed. Dates are in UTC.
        </footer>
      </article>
    </div>
  );
}
