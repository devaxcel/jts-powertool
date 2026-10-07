"use client";

import { useCallback, useEffect, useState } from "react";
import { ExternalLink, Link2, Loader2, RefreshCw, Unlink, Receipt } from "lucide-react";
import { QuickBooksStatus, disconnectQuickBooks, fetchQuickBooksStatus, startQuickBooksConnect } from "@/lib/api";
import { Alert, Badge, ConfirmDialog, LoadingState, Section, btn } from "@/components/ui";
import { formatLocalDateTime } from "@/lib/types";

/** JTS Admin: connect the QuickBooks company that client invoices are sent to. */
export function QuickBooksCard() {
  const [status, setStatus] = useState<QuickBooksStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [confirmOff, setConfirmOff] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error" | "info"; message: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await fetchQuickBooksStatus());
    } catch (e: any) {
      setFeedback({ type: "error", message: e.message });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const onFocus = () => load(); // coming back from the QuickBooks tab
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [load]);

  async function handleConnect() {
    setBusy(true);
    setFeedback(null);
    try {
      const { url } = await startQuickBooksConnect();
      window.open(url, "_blank", "noopener");
      setFeedback({ type: "info", message: "QuickBooks opened in a new tab. Sign in, choose your company, click Connect, then come back here." });
    } catch (e: any) {
      setFeedback({ type: "error", message: e.message });
    } finally {
      setBusy(false);
    }
  }

  async function handleDisconnect() {
    setBusy(true);
    try {
      const res = await disconnectQuickBooks();
      setFeedback({ type: "success", message: res.message });
      setConfirmOff(false);
      await load();
    } catch (e: any) {
      setFeedback({ type: "error", message: e.message });
    } finally {
      setBusy(false);
    }
  }

  if (loading && !status) return <LoadingState label="Checking QuickBooks..." />;

  const connected = Boolean(status?.connected);
  const expired = status?.status === "expired";

  return (
    <Section
      icon={Receipt}
      title={
        <span className="inline-flex items-center gap-2">
          QuickBooks
          <Badge tone={connected ? "green" : expired ? "rose" : "gray"}>{connected ? "Connected" : expired ? "Needs reconnecting" : "Not connected"}</Badge>
          {status && <Badge tone="gray">{status.environment === "production" ? "Live" : "Sandbox (test)"}</Badge>}
        </span>
      }
      description="Send client invoices from Usage & Billing to your QuickBooks company. The customer and a service item are created for you the first time. It is one-way: marking an invoice paid here does not change QuickBooks."
      actions={
        <>
          <button onClick={load} className={btn.secondary} disabled={loading} aria-label="Refresh QuickBooks status">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
          {status?.configured && (
            <button onClick={handleConnect} disabled={busy} className={btn.primary}>
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Link2 className="h-3.5 w-3.5" />}
              {connected || expired ? "Reconnect" : "Connect QuickBooks"}
            </button>
          )}
        </>
      }
      bodyClassName="p-5 space-y-4"
    >
      {feedback && (
        <Alert type={feedback.type} onClose={() => setFeedback(null)}>
          {feedback.message}
        </Alert>
      )}

      {!status?.configured ? (
        <Alert type="info" title="One-time setup needed">
          <ol className="list-decimal list-inside space-y-1 text-xs">
            <li>
              Create an app at <span className="font-mono">developer.intuit.com</span> (QuickBooks Online, Accounting scope).
            </li>
            <li>
              Add this redirect link in the app: <span className="font-mono break-all">{status?.redirect_uri}</span>
            </li>
            <li>
              On the <strong>API Keys</strong> page save <span className="font-mono">QUICKBOOKS_CLIENT_ID</span> and{" "}
              <span className="font-mono">QUICKBOOKS_CLIENT_SECRET</span>. Add <span className="font-mono">QUICKBOOKS_ENVIRONMENT = production</span> when you move from the test sandbox to your real company.
            </li>
            <li>Come back here and click Connect QuickBooks.</li>
          </ol>
        </Alert>
      ) : connected ? (
        <dl className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
          <div>
            <dt className="text-gray-500">Company</dt>
            <dd className="font-semibold text-gray-900 mt-0.5">{status.company_name || "—"}</dd>
          </div>
          <div>
            <dt className="text-gray-500">Connected by</dt>
            <dd className="text-gray-800 mt-0.5">{status.connected_by || "—"}</dd>
          </div>
          <div>
            <dt className="text-gray-500">Connected</dt>
            <dd className="text-gray-800 mt-0.5" suppressHydrationWarning>
              {status.connected_at ? formatLocalDateTime(status.connected_at) : "—"}
            </dd>
          </div>
        </dl>
      ) : expired ? (
        <Alert type="warning" title="QuickBooks access expired or was removed">
          Click Reconnect so invoices can be sent again.
        </Alert>
      ) : (
        <p className="text-sm text-gray-600">Not connected yet. Click “Connect QuickBooks”, sign in and choose the company you invoice from.</p>
      )}

      {(connected || expired) && (
        <div className="flex flex-wrap items-center justify-between gap-2 pt-3 border-t border-gray-100">
          <a href="https://app.qbo.intuit.com" target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 text-xs font-semibold text-[#088ADA] hover:underline">
            Open QuickBooks <ExternalLink className="h-3 w-3" />
          </a>
          <button onClick={() => setConfirmOff(true)} className={btn.dangerSoft} disabled={busy}>
            <Unlink className="h-3.5 w-3.5" /> Disconnect
          </button>
        </div>
      )}

      <ConfirmDialog open={confirmOff} busy={busy} title="Disconnect QuickBooks?" confirmLabel="Disconnect" confirmClass={btn.danger} onCancel={() => setConfirmOff(false)} onConfirm={handleDisconnect}>
        <p>Invoices already sent stay in QuickBooks. New ones can&apos;t be sent until you connect again.</p>
      </ConfirmDialog>
    </Section>
  );
}
