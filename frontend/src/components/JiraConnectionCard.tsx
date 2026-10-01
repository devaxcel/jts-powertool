"use client";

import { useCallback, useEffect, useState } from "react";
import { SquareKanban, Loader2, Link2, Unlink, RefreshCw } from "lucide-react";
import {
  JiraConnectionStatus,
  createJiraConnectLink,
  disconnectJira,
  fetchJiraStatus,
  setJiraDefaultProject,
} from "@/lib/api";
import { formatLocalDateTime } from "@/lib/types";
import { Alert, ConfirmDialog, btn } from "@/components/ui";

export function JiraConnectionCard({ folderId, canEdit }: { folderId: number | string; canEdit: boolean }) {
  const id = Number(folderId);
  const [status, setStatus] = useState<JiraConnectionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error" | "info"; message: string } | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await fetchJiraStatus(id));
    } catch (e: any) {
      setFeedback({ type: "error", message: e.message });
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
    // Coming back from the Atlassian tab: refresh to show the new connection.
    const onFocus = () => load();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [load]);

  async function handleConnect() {
    setBusy(true);
    setFeedback(null);
    try {
      const { url } = await createJiraConnectLink(id);
      window.open(url, "_blank", "noopener");
      setFeedback({
        type: "info",
        message: "Atlassian opened in a new tab. Sign in, choose your Jira site, click Accept, then come back here.",
      });
    } catch (e: any) {
      setFeedback({ type: "error", message: e.message });
    } finally {
      setBusy(false);
    }
  }

  async function handleDefaultProject(value: string) {
    setBusy(true);
    try {
      await setJiraDefaultProject(id, value || null);
      setFeedback({ type: "success", message: value ? `New Jira issues go to ${value} unless told otherwise.` : "No default project: the bot will ask which project to use." });
      await load();
    } catch (e: any) {
      setFeedback({ type: "error", message: e.message });
    } finally {
      setBusy(false);
    }
  }

  async function handleDisconnect() {
    setBusy(true);
    try {
      const res = await disconnectJira(id);
      setFeedback({ type: "success", message: res.message });
      setConfirmDisconnect(false);
      await load();
    } catch (e: any) {
      setFeedback({ type: "error", message: e.message });
    } finally {
      setBusy(false);
    }
  }

  const conn = status?.connection;
  const connected = Boolean(status?.connected && conn);

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="h-10 w-10 rounded-xl bg-[#0052CC] text-white flex items-center justify-center shrink-0">
            <SquareKanban className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-gray-800 flex items-center gap-2">
              Jira
              {!loading && (
                <span
                  className={`text-[11px] font-semibold px-2 py-0.5 rounded-full border ${
                    connected
                      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                      : conn
                      ? "bg-rose-50 text-rose-700 border-rose-200"
                      : "bg-gray-100 text-gray-600 border-gray-200"
                  }`}
                >
                  {connected ? "Connected" : conn ? "Needs reconnecting" : "Not connected"}
                </span>
              )}
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              The bot works in this client&apos;s own Jira. It can search and read issues; creating, changing, commenting and moving issues always need approval. In Slack, anyone can type{" "}
              <span className="font-mono">@bot connect my jira</span>.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button onClick={load} className={btn.secondary} disabled={loading} aria-label="Refresh Jira status">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
          {canEdit && status?.configured && (
            <button onClick={handleConnect} disabled={busy} className={btn.primary}>
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Link2 className="h-3.5 w-3.5" />}
              {connected || conn ? "Change connection" : "Connect Jira"}
            </button>
          )}
        </div>
      </div>

      {feedback && (
        <Alert type={feedback.type} onClose={() => setFeedback(null)}>
          {feedback.message}
        </Alert>
      )}

      {loading && !status ? (
        <p className="text-xs text-gray-500 flex items-center gap-2">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Checking Jira…
        </p>
      ) : !status?.configured ? (
        <Alert type="info">Jira connection isn&apos;t set up on JTS PowerTool yet. A JTS administrator needs to register the Atlassian app first.</Alert>
      ) : conn && !connected ? (
        <Alert type="warning" title="Jira access expired or was removed">
          Connect Jira again so the bot can keep working.
        </Alert>
      ) : connected && conn ? (
        <div className="space-y-3">
          {status?.error && <Alert type="warning">{status.error}</Alert>}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
            <div>
              <p className="text-gray-500">Jira site</p>
              <p className="font-semibold text-gray-800">{conn.site_name || conn.site_url}</p>
              {conn.site_url && <p className="text-gray-500 truncate">{conn.site_url}</p>}
            </div>
            <div>
              <p className="text-gray-500">Projects the bot can see</p>
              <p className="font-semibold text-gray-800">{status?.projects.length ?? 0}</p>
            </div>
            <div>
              <p className="text-gray-500">Connected</p>
              <p className="text-gray-800">{conn.updated_at ? formatLocalDateTime(conn.updated_at) : "—"}</p>
            </div>
          </div>

          <label className="block space-y-1">
            <span className="text-xs font-semibold text-gray-700">Default project for new issues</span>
            <select
              value={conn.default_project || ""}
              onChange={(e) => handleDefaultProject(e.target.value)}
              disabled={!canEdit || busy}
              className="w-full sm:max-w-md px-3 py-2 bg-white border border-gray-300 rounded-xl text-sm text-gray-800 focus:outline-none focus:border-[#088ADA] disabled:bg-gray-50"
            >
              <option value="">None (the bot asks which project)</option>
              {(status?.projects || []).map((p) => (
                <option key={p.key} value={p.key}>
                  {p.key} · {p.name}
                </option>
              ))}
            </select>
          </label>

          {canEdit && (
            <div className="flex flex-wrap items-center justify-end gap-2 pt-1">
              <button onClick={() => setConfirmDisconnect(true)} className={btn.dangerSoft} disabled={busy}>
                <Unlink className="h-3.5 w-3.5" /> Disconnect
              </button>
            </div>
          )}
        </div>
      ) : (
        <p className="text-xs text-gray-600">
          Not connected yet. {canEdit ? "Click “Connect Jira”, or" : "Ask your Client Admin, or"} type{" "}
          <span className="font-mono">@bot connect my jira</span> in one of this client&apos;s Slack channels.
        </p>
      )}

      <ConfirmDialog
        open={confirmDisconnect}
        busy={busy}
        title="Disconnect Jira?"
        confirmLabel="Disconnect"
        confirmClass={btn.danger}
        onCancel={() => setConfirmDisconnect(false)}
        onConfirm={handleDisconnect}
      >
        <p>The bot stops using this Jira site. To remove access completely, also remove “JTS PowerTool” under Connected apps in your Atlassian account.</p>
      </ConfirmDialog>
    </section>
  );
}
