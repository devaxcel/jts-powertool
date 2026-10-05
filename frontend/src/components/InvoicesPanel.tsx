"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { FilePlus2, FileText, CheckCircle2, Ban, Printer, RefreshCw, Loader2, X } from "lucide-react";
import {
  fetchInvoices,
  fetchFolders,
  fetchOrganizations,
  previewInvoice,
  createInvoice,
  markInvoicePaid,
  voidInvoice,
} from "@/lib/api";
import { ChannelFolder, Invoice, InvoicePreview } from "@/lib/types";
import { DataTable } from "@/components/DataTable";
import { Alert, ConfirmDialog, EmptyState, LoadingState, btn, inputClass } from "@/components/ui";
import { describeRange, formatUsd, isoDay, lastMonthRange, monthRange } from "@/lib/periods";

const STATUS_FILTERS = [
  { value: "all", label: "All" },
  { value: "unpaid", label: "Unpaid" },
  { value: "overdue", label: "Overdue" },
  { value: "paid", label: "Paid" },
  { value: "void", label: "Void" },
];

export function InvoiceStatusBadge({ status }: { status: Invoice["display_status"] }) {
  const map: Record<string, { label: string; cls: string }> = {
    unpaid: { label: "Unpaid", cls: "bg-amber-50 text-amber-700 border-amber-200" },
    overdue: { label: "Overdue", cls: "bg-rose-50 text-rose-700 border-rose-200" },
    paid: { label: "Paid", cls: "bg-emerald-50 text-emerald-700 border-emerald-200" },
    void: { label: "Void", cls: "bg-gray-100 text-gray-500 border-gray-200" },
  };
  const s = map[status] || map.unpaid;
  return <span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold border ${s.cls}`}>{s.label}</span>;
}

function Modal({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm flex items-center justify-center p-4 overflow-y-auto" onClick={onClose}>
      <div
        role="dialog"
        aria-modal="true"
        className="bg-white rounded-2xl max-w-2xl w-full p-6 shadow-2xl border border-gray-200 space-y-4 max-h-[92vh] overflow-y-auto my-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold text-gray-900">{title}</h3>
          <button onClick={onClose} className="p-1 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100" aria-label="Close">
            <X className="h-4 w-4" />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-semibold text-gray-700">{label}</span>
      {children}
      {hint && <span className="block text-[11px] text-gray-500">{hint}</span>}
    </label>
  );
}

function CreateInvoiceModal({ onClose, onCreated }: { onClose: () => void; onCreated: (message: string) => void }) {
  const last = lastMonthRange();
  const [folders, setFolders] = useState<ChannelFolder[]>([]);
  const [orgs, setOrgs] = useState<Array<{ id: number; name: string; billing_email?: string; email?: string }>>([]);
  const [folderId, setFolderId] = useState<number | "">("");
  const [mode, setMode] = useState<"month" | "custom">("month");
  const [month, setMonth] = useState(last.start.slice(0, 7));
  const [customStart, setCustomStart] = useState(last.start);
  const [customEnd, setCustomEnd] = useState(last.end);
  const [markup, setMarkup] = useState("0");
  const [dueDays, setDueDays] = useState("14");
  const [orgId, setOrgId] = useState<number | "">("");
  const [orgTouched, setOrgTouched] = useState(false);
  const [notes, setNotes] = useState("");
  const [preview, setPreview] = useState<InvoicePreview | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    fetchFolders().then(setFolders).catch(() => setFolders([]));
    fetchOrganizations()
      .then((r) => setOrgs(r.organizations || []))
      .catch(() => setOrgs([]));
  }, []);

  const period = useMemo(() => {
    if (mode === "month") {
      const [y, m] = month.split("-").map(Number);
      return y && m ? monthRange(y, m - 1) : { start: "", end: "" };
    }
    return { start: customStart, end: customEnd };
  }, [mode, month, customStart, customEnd]);

  const markupNum = Number(markup);
  const dueNum = Number(dueDays);
  const inputsValid =
    folderId !== "" &&
    Boolean(period.start && period.end) &&
    period.end >= period.start &&
    Number.isFinite(markupNum) &&
    markupNum >= 0 &&
    markupNum <= 1000 &&
    Number.isInteger(dueNum) &&
    dueNum >= 0 &&
    dueNum <= 365;

  useEffect(() => {
    if (!inputsValid) {
      setPreview(null);
      return;
    }
    let cancelled = false;
    setPreviewing(true);
    setError(null);
    const t = setTimeout(() => {
      previewInvoice({ folder_id: Number(folderId), period_start: period.start, period_end: period.end, markup_percent: markupNum })
        .then((p) => {
          if (cancelled) return;
          setPreview(p);
          if (!orgTouched) setOrgId(p.suggested_organization_id ?? "");
        })
        .catch((e) => !cancelled && setError(e.message))
        .finally(() => !cancelled && setPreviewing(false));
    }, 300);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [folderId, period.start, period.end, markupNum, inputsValid, orgTouched]);

  async function submit() {
    if (!inputsValid || !preview) return;
    setSaving(true);
    setError(null);
    try {
      const res = await createInvoice({
        folder_id: Number(folderId),
        period_start: period.start,
        period_end: period.end,
        organization_id: orgId === "" ? null : Number(orgId),
        markup_percent: markupNum,
        due_days: dueNum,
        notes: notes.trim() || undefined,
      });
      onCreated(res.message);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  const selectedOrg = orgs.find((o) => o.id === orgId);

  return (
    <Modal title="Create invoice" onClose={onClose}>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Client">
          <select
            className={inputClass}
            value={folderId}
            onChange={(e) => {
              setFolderId(e.target.value ? Number(e.target.value) : "");
              setOrgTouched(false); // a new client: pick up that client's organization again
            }}
          >
            <option value="">Choose a client...</option>
            {folders.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </select>
        </Field>

        <Field label="Bill to (organization)" hint={
            selectedOrg
              ? `${!orgTouched && preview?.suggested_organization_id === selectedOrg.id ? "Filled in from the client. " : ""}Invoice email: ${selectedOrg.billing_email || selectedOrg.email || "not set"}`
              : "Optional. Shown on the invoice. Link an organization to the client under Clients & Channels to fill this in automatically."
          }>
          <select
            className={inputClass}
            value={orgId}
            onChange={(e) => {
              setOrgTouched(true);
              setOrgId(e.target.value ? Number(e.target.value) : "");
            }}
          >
            <option value="">No organization</option>
            {orgs.map((o) => (
              <option key={o.id} value={o.id}>
                {o.name}
              </option>
            ))}
          </select>
        </Field>

        <div className="sm:col-span-2 space-y-2">
          <div className="flex items-center gap-1 bg-gray-100 p-1 rounded-lg w-fit text-xs font-medium">
            {(["month", "custom"] as const).map((m) => (
              <button
                key={m}
                type="button"
                onClick={() => setMode(m)}
                className={`px-3 py-1 rounded-md ${mode === m ? "bg-white shadow-sm text-gray-900" : "text-gray-600"}`}
              >
                {m === "month" ? "Whole month" : "Custom dates"}
              </button>
            ))}
          </div>
          {mode === "month" ? (
            <Field label="Month" hint="Dates are in UTC.">
              <input type="month" className={inputClass} value={month} onChange={(e) => setMonth(e.target.value)} />
            </Field>
          ) : (
            <div className="grid grid-cols-2 gap-3">
              <Field label="From">
                <input type="date" className={inputClass} value={customStart} onChange={(e) => setCustomStart(e.target.value)} />
              </Field>
              <Field label="To (included)">
                <input type="date" className={inputClass} value={customEnd} onChange={(e) => setCustomEnd(e.target.value)} />
              </Field>
            </div>
          )}
        </div>

        <Field label="Markup %" hint="0 = bill exactly the AI cost.">
          <input type="number" min={0} max={1000} step="0.5" className={inputClass} value={markup} onChange={(e) => setMarkup(e.target.value)} />
        </Field>
        <Field label="Payment due in (days)">
          <input type="number" min={0} max={365} step="1" className={inputClass} value={dueDays} onChange={(e) => setDueDays(e.target.value)} />
        </Field>
        <div className="sm:col-span-2">
          <Field label="Note on the invoice (optional)">
            <textarea className={inputClass} rows={2} maxLength={2000} value={notes} onChange={(e) => setNotes(e.target.value)} />
          </Field>
        </div>
      </div>

      {/* Preview */}
      <div className="rounded-xl border border-gray-200 bg-gray-50 p-4 space-y-3">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-gray-700">
            Preview {period.start && period.end ? `· ${describeRange(period)}` : ""}
          </span>
          {previewing && <Loader2 className="h-4 w-4 animate-spin text-[#088ADA]" />}
        </div>
        {!inputsValid && <p className="text-xs text-gray-500">Choose a client and a valid period to see the amount.</p>}
        {preview && inputsValid && (
          <>
            {preview.line_items.length === 0 ? (
              <p className="text-xs text-gray-600">
                No billable AI usage in this period
                {preview.channel_count === 0 ? " (this client has no channels yet)" : ""}. The invoice would be $0.00.
              </p>
            ) : (
              <table className="w-full text-xs">
                <thead className="text-gray-500">
                  <tr>
                    <th className="text-left font-medium py-1">Channel</th>
                    <th className="text-right font-medium py-1">AI replies</th>
                    <th className="text-right font-medium py-1">AI cost</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {preview.line_items.map((li) => (
                    <tr key={li.channel_id}>
                      <td className="py-1 text-gray-800">{li.channel_name}</td>
                      <td className="py-1 text-right font-mono">{li.replies.toLocaleString()}</td>
                      <td className="py-1 text-right font-mono">${li.cost_usd.toFixed(4)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            <div className="flex items-center justify-between border-t border-gray-200 pt-2 text-sm">
              <span className="text-gray-600">
                AI cost {formatUsd(preview.usage_cost_usd)}
                {preview.markup_percent > 0 && ` + ${preview.markup_percent}% markup`}
              </span>
              <span className="font-semibold text-gray-900">Total {formatUsd(preview.amount_usd)}</span>
            </div>
            {preview.overlapping_invoice && (
              <Alert type="warning">
                Invoice {preview.overlapping_invoice} already covers part of this period for this client. Void it first if you need to
                re-issue.
              </Alert>
            )}
          </>
        )}
      </div>

      {error && <Alert type="error">{error}</Alert>}

      <div className="flex items-center justify-end gap-2">
        <button type="button" onClick={onClose} className={btn.secondary}>
          Cancel
        </button>
        <button
          type="button"
          onClick={submit}
          disabled={!inputsValid || !preview || previewing || saving || Boolean(preview?.overlapping_invoice)}
          className={btn.primary}
        >
          {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          Create invoice
        </button>
      </div>
    </Modal>
  );
}

function MarkPaidModal({ invoice, onClose, onDone }: { invoice: Invoice; onClose: () => void; onDone: (m: string) => void }) {
  const [paidOn, setPaidOn] = useState(isoDay(new Date()));
  const [reference, setReference] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit() {
    setSaving(true);
    setError(null);
    try {
      const res = await markInvoicePaid(invoice.id, { paid_on: paidOn, reference: reference.trim() || undefined });
      onDone(res.message);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal title={`Mark ${invoice.invoice_number} as paid`} onClose={onClose}>
      <p className="text-sm text-gray-600">
        {invoice.folder_name} · {formatUsd(invoice.amount_usd)} · {describeRange({ start: invoice.period_start, end: invoice.period_end })}
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <Field label="Payment received on">
          <input type="date" className={inputClass} value={paidOn} onChange={(e) => setPaidOn(e.target.value)} />
        </Field>
        <Field label="Payment reference (optional)" hint="e.g. bank transfer ID or cheque number">
          <input className={inputClass} maxLength={255} value={reference} onChange={(e) => setReference(e.target.value)} />
        </Field>
      </div>
      {error && <Alert type="error">{error}</Alert>}
      <div className="flex items-center justify-end gap-2">
        <button type="button" onClick={onClose} className={btn.secondary}>
          Cancel
        </button>
        <button type="button" onClick={submit} disabled={saving || !paidOn} className={btn.success}>
          {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
          Mark as paid
        </button>
      </div>
    </Modal>
  );
}

export function InvoicesPanel({ isMasterAdmin }: { isMasterAdmin: boolean }) {
  const [invoices, setInvoices] = useState<Invoice[]>([]);
  const [loading, setLoading] = useState(true);
  const [status, setStatus] = useState("all");
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [creating, setCreating] = useState(false);
  const [paying, setPaying] = useState<Invoice | null>(null);
  const [voiding, setVoiding] = useState<Invoice | null>(null);
  const [voidReason, setVoidReason] = useState("");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setInvoices(await fetchInvoices(status));
    } catch (e: any) {
      setFeedback({ type: "error", message: e.message });
    } finally {
      setLoading(false);
    }
  }, [status]);

  useEffect(() => {
    load();
  }, [load]);

  const totals = useMemo(() => {
    const open = invoices.filter((i) => i.status === "unpaid");
    return {
      outstanding: open.reduce((s, i) => s + i.amount_usd, 0),
      overdue: open.filter((i) => i.display_status === "overdue").reduce((s, i) => s + i.amount_usd, 0),
      openCount: open.length,
    };
  }, [invoices]);

  async function confirmVoid() {
    if (!voiding) return;
    setBusy(true);
    try {
      const res = await voidInvoice(voiding.id, voidReason.trim() || undefined);
      setFeedback({ type: "success", message: res.message });
      setVoiding(null);
      setVoidReason("");
      await load();
    } catch (e: any) {
      setFeedback({ type: "error", message: e.message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-1 bg-white border border-gray-200 rounded-xl p-1 overflow-x-auto">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.value}
              onClick={() => setStatus(f.value)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition ${
                status === f.value ? "bg-[#088ADA] text-white shadow-sm" : "text-gray-600 hover:text-gray-900 hover:bg-gray-100"
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-2">
          <button onClick={load} className={btn.secondary}>
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            Refresh
          </button>
          {isMasterAdmin && (
            <button onClick={() => setCreating(true)} className={btn.primary}>
              <FilePlus2 className="h-3.5 w-3.5" />
              Create invoice
            </button>
          )}
        </div>
      </div>

      {feedback && (
        <Alert type={feedback.type} onClose={() => setFeedback(null)}>
          {feedback.message}
        </Alert>
      )}

      {totals.openCount > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="bg-white border border-gray-200 rounded-2xl p-4">
            <p className="text-xs text-gray-500">Waiting for payment ({totals.openCount})</p>
            <p className="text-xl font-semibold text-gray-900 mt-1">{formatUsd(totals.outstanding)}</p>
          </div>
          <div className="bg-white border border-gray-200 rounded-2xl p-4">
            <p className="text-xs text-gray-500">Overdue</p>
            <p className={`text-xl font-semibold mt-1 ${totals.overdue > 0 ? "text-rose-600" : "text-gray-900"}`}>{formatUsd(totals.overdue)}</p>
          </div>
        </div>
      )}

      {loading && invoices.length === 0 ? (
        <LoadingState label="Loading invoices..." />
      ) : invoices.length === 0 ? (
        <EmptyState
          icon={FileText}
          title={status === "all" ? "No invoices yet" : "No invoices with this status"}
          description={
            isMasterAdmin
              ? "Create a monthly invoice for a client from its billable AI usage. You record payments here when they arrive."
              : "Invoices from JTS will appear here."
          }
          action={
            isMasterAdmin && status === "all" ? (
              <button onClick={() => setCreating(true)} className={btn.primary}>
                <FilePlus2 className="h-3.5 w-3.5" />
                Create invoice
              </button>
            ) : undefined
          }
        />
      ) : (
        <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
          <DataTable
            rows={invoices}
            rowKey={(i) => i.id}
            itemLabel="invoices"
            searchPlaceholder="Search invoice number or client"
            initialSort={{ key: "period", dir: "desc" }}
            columns={[
              {
                key: "invoice_number",
                header: "Invoice",
                className: "font-mono font-semibold text-gray-800 whitespace-nowrap",
              },
              ...(isMasterAdmin
                ? [
                    {
                      key: "folder_name",
                      header: "Client",
                      className: "font-medium text-gray-800",
                      searchValue: (i: Invoice) => `${i.folder_name} ${i.bill_to?.name || ""}`,
                      render: (i: Invoice) => (
                        <>
                          <div>{i.folder_name}</div>
                          {i.bill_to?.name && <div className="text-[11px] text-gray-500">{i.bill_to.name}</div>}
                        </>
                      ),
                    },
                  ]
                : []),
              {
                key: "period",
                header: "Period",
                sortValue: (i) => i.period_start,
                searchValue: () => "",
                className: "whitespace-nowrap",
                render: (i) => describeRange({ start: i.period_start, end: i.period_end }),
              },
              {
                key: "amount_usd",
                header: "Amount",
                align: "right",
                searchValue: () => "",
                className: "font-mono font-semibold text-gray-900",
                render: (i) => formatUsd(i.amount_usd),
              },
              {
                key: "display_status",
                header: "Status",
                render: (i) => (
                  <div className="space-y-0.5">
                    <InvoiceStatusBadge status={i.display_status} />
                    {i.status === "paid" && i.paid_at && <div className="text-[11px] text-gray-500">on {i.paid_at}</div>}
                  </div>
                ),
              },
              {
                key: "due_date",
                header: "Due",
                searchValue: () => "",
                className: "whitespace-nowrap text-gray-600",
                render: (i) => (i.status === "unpaid" ? i.due_date || "—" : "—"),
              },
              {
                key: "actions",
                header: "Actions",
                sortable: false,
                searchValue: () => "",
                align: "right",
                render: (i) => (
                  <div className="flex items-center justify-end gap-1.5">
                    <Link href={`/billing/invoices/${i.id}`} className={btn.secondary} title="View or print">
                      <Printer className="h-3.5 w-3.5" />
                      View
                    </Link>
                    {isMasterAdmin && i.status === "unpaid" && (
                      <>
                        <button onClick={() => setPaying(i)} className={btn.success}>
                          <CheckCircle2 className="h-3.5 w-3.5" />
                          Mark paid
                        </button>
                        <button onClick={() => setVoiding(i)} className={btn.dangerSoft} title="Cancel this invoice">
                          <Ban className="h-3.5 w-3.5" />
                          Void
                        </button>
                      </>
                    )}
                  </div>
                ),
              },
            ]}
          />
        </div>
      )}

      {creating && (
        <CreateInvoiceModal
          onClose={() => setCreating(false)}
          onCreated={(m) => {
            setCreating(false);
            setFeedback({ type: "success", message: m });
            load();
          }}
        />
      )}
      {paying && (
        <MarkPaidModal
          invoice={paying}
          onClose={() => setPaying(null)}
          onDone={(m) => {
            setPaying(null);
            setFeedback({ type: "success", message: m });
            load();
          }}
        />
      )}
      <ConfirmDialog
        open={Boolean(voiding)}
        busy={busy}
        title={`Void ${voiding?.invoice_number}?`}
        confirmLabel="Void invoice"
        confirmClass={btn.danger}
        onCancel={() => {
          setVoiding(null);
          setVoidReason("");
        }}
        onConfirm={confirmVoid}
      >
        <p>The invoice stays on record as void and can no longer be paid. You can then create a corrected one for the same period.</p>
        <label className="block space-y-1">
          <span className="text-xs font-semibold text-gray-700">Reason (optional)</span>
          <input className={inputClass} maxLength={1000} value={voidReason} onChange={(e) => setVoidReason(e.target.value)} />
        </label>
      </ConfirmDialog>
    </div>
  );
}
