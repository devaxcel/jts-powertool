"use client";

import { useEffect, useState } from "react";
import { Download, FileBarChart, Loader2 } from "lucide-react";
import { downloadMonthlyReport, fetchFolders } from "@/lib/api";
import { ChannelFolder } from "@/lib/types";
import { Alert, Section, btn, inputClass } from "@/components/ui";

/** The month before this one, as YYYY-MM (the usual month to report on). */
function lastMonth(): string {
  const d = new Date();
  const prev = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() - 1, 1));
  return `${prev.getUTCFullYear()}-${String(prev.getUTCMonth() + 1).padStart(2, "0")}`;
}

function thisMonth(): string {
  const d = new Date();
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

/** Download the month's usage and billing as a PDF. JTS Admins pick one client or all; a Client Admin gets their own client. */
export function MonthlyReportCard({ isMasterAdmin }: { isMasterAdmin: boolean }) {
  const [month, setMonth] = useState(lastMonth());
  const [folderId, setFolderId] = useState("");
  const [folders, setFolders] = useState<ChannelFolder[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isMasterAdmin) fetchFolders().then(setFolders).catch(() => setFolders([]));
  }, [isMasterAdmin]);

  async function handleDownload() {
    setBusy(true);
    setError(null);
    try {
      await downloadMonthlyReport(month, isMasterAdmin && folderId ? Number(folderId) : undefined);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <Section
      icon={FileBarChart}
      title="Monthly billing report"
      description="A PDF for one calendar month: AI replies, tokens and cost by channel and by person, the month's invoices and the monthly budget. It uses the same figures as the invoices."
      bodyClassName="p-5 space-y-3"
    >
      {error && (
        <Alert type="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      <div className="flex flex-col sm:flex-row sm:items-end gap-3">
        <label className="block space-y-1">
          <span className="text-xs font-semibold text-gray-700">Month</span>
          <input type="month" value={month} max={thisMonth()} onChange={(e) => setMonth(e.target.value)} className={inputClass} />
        </label>
        {isMasterAdmin && (
          <label className="block space-y-1 sm:min-w-[220px]">
            <span className="text-xs font-semibold text-gray-700">Client</span>
            <select value={folderId} onChange={(e) => setFolderId(e.target.value)} className={inputClass}>
              <option value="">All clients</option>
              {folders.map((f) => (
                <option key={f.id} value={f.id}>
                  {f.name}
                </option>
              ))}
            </select>
          </label>
        )}
        <button onClick={handleDownload} disabled={busy || !month} className={btn.primary}>
          {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
          <span>Download PDF</span>
        </button>
      </div>
      {month === thisMonth() && <p className="text-[11px] text-gray-500">This month isn&apos;t finished, so the report shows usage so far.</p>}
    </Section>
  );
}
