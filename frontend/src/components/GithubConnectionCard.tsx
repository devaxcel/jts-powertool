"use client";

import { useCallback, useEffect, useState } from "react";
import { GitBranch, Loader2, Link2, Unlink, RefreshCw, Lock, Globe2 } from "lucide-react";
import {
  GithubConnectionStatus,
  createGithubConnectLink,
  disconnectGithub,
  fetchGithubStatus,
  setGithubDefaultRepo,
} from "@/lib/api";
import { formatLocalDateTime } from "@/lib/types";
import { Alert, ConfirmDialog, btn } from "@/components/ui";

export function GithubConnectionCard({ folderId, canEdit }: { folderId: number | string; canEdit: boolean }) {
  const id = Number(folderId);
  const [status, setStatus] = useState<GithubConnectionStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error" | "info"; message: string } | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await fetchGithubStatus(id));
    } catch (e: any) {
      setFeedback({ type: "error", message: e.message });
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
    // Coming back from the GitHub tab: refresh to show the new connection.
    const onFocus = () => load();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [load]);

  async function handleConnect() {
    setBusy(true);
    setFeedback(null);
    try {
      const { url } = await createGithubConnectLink(id);
      window.open(url, "_blank", "noopener");
      setFeedback({
        type: "info",
        message: "GitHub opened in a new tab. Choose the account and repositories, click Install and Authorize, then come back here.",
      });
    } catch (e: any) {
      setFeedback({ type: "error", message: e.message });
    } finally {
      setBusy(false);
    }
  }

  async function handleDefaultRepo(value: string) {
    setBusy(true);
    try {
      await setGithubDefaultRepo(id, value || null);
      setFeedback({ type: "success", message: value ? `The bot will use ${value} unless told otherwise.` : "No default repository: the bot will ask which one to use." });
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
      const res = await disconnectGithub(id);
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
          <div className="h-10 w-10 rounded-xl bg-gray-900 text-white flex items-center justify-center shrink-0">
            <GitBranch className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-gray-800 flex items-center gap-2">
              GitHub
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
              The bot works on this client&apos;s own GitHub. Every change still needs approval. In Slack, anyone can type{" "}
              <span className="font-mono">@bot connect my github</span>.
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button onClick={load} className={btn.secondary} disabled={loading} aria-label="Refresh GitHub status">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
          {canEdit && status?.configured && (
            <button onClick={handleConnect} disabled={busy} className={btn.primary}>
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Link2 className="h-3.5 w-3.5" />}
              {connected ? "Change connection" : "Connect GitHub"}
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
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Checking GitHub…
        </p>
      ) : !status?.configured ? (
        <Alert type="info">GitHub connection isn&apos;t set up on JTS PowerTool yet. A JTS administrator needs to register the GitHub App first.</Alert>
      ) : conn && !connected ? (
        <Alert type="warning" title="The GitHub app was removed or suspended on GitHub">
          Connect GitHub again so the bot can keep working.
        </Alert>
      ) : connected && conn ? (
        <div className="space-y-3">
          {status?.error && <Alert type="warning">{status.error}</Alert>}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 text-xs">
            <div>
              <p className="text-gray-500">Account</p>
              <p className="font-semibold text-gray-800">
                {conn.account_login} <span className="font-normal text-gray-500">({conn.account_type === "Organization" ? "organization" : "personal"})</span>
              </p>
            </div>
            <div>
              <p className="text-gray-500">Repositories the bot can use</p>
              <p className="font-semibold text-gray-800">
                {conn.repository_selection === "all" ? "All repositories" : `${status?.repos.length ?? conn.repo_count ?? 0} selected`}
              </p>
            </div>
            <div>
              <p className="text-gray-500">Connected</p>
              <p className="text-gray-800">{conn.updated_at ? formatLocalDateTime(conn.updated_at) : "—"}</p>
            </div>
          </div>

          <label className="block space-y-1">
            <span className="text-xs font-semibold text-gray-700">Default repository</span>
            <select
              value={conn.default_repo || ""}
              onChange={(e) => handleDefaultRepo(e.target.value)}
              disabled={!canEdit || busy}
              className="w-full sm:max-w-md px-3 py-2 bg-white border border-gray-300 rounded-xl text-sm text-gray-800 focus:outline-none focus:border-[#088ADA] disabled:bg-gray-50"
            >
              <option value="">None (the bot asks which repository)</option>
              {(status?.repos || []).map((r) => (
                <option key={r.full_name} value={r.full_name}>
                  {r.full_name}
                  {r.private ? " (private)" : ""}
                </option>
              ))}
            </select>
          </label>

          {(status?.repos?.length || 0) > 0 && (
            <ul className="flex flex-wrap gap-1.5">
              {status!.repos.slice(0, 12).map((r) => (
                <li key={r.full_name} className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-gray-100 border border-gray-200 text-gray-700">
                  {r.private ? <Lock className="h-3 w-3" /> : <Globe2 className="h-3 w-3" />}
                  {r.full_name}
                </li>
              ))}
              {status!.repos.length > 12 && <li className="text-[11px] text-gray-500">+{status!.repos.length - 12} more</li>}
            </ul>
          )}

          {canEdit && (
            <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
              <p className="text-[11px] text-gray-500">To add or remove repositories, use “Change connection”.</p>
              <button onClick={() => setConfirmDisconnect(true)} className={btn.dangerSoft} disabled={busy}>
                <Unlink className="h-3.5 w-3.5" /> Disconnect
              </button>
            </div>
          )}
        </div>
      ) : (
        <p className="text-xs text-gray-600">
          Not connected yet. {canEdit ? "Click “Connect GitHub”, or" : "Ask your Client Admin, or"} type{" "}
          <span className="font-mono">@bot connect my github</span> in one of this client&apos;s Slack channels.
        </p>
      )}

      <ConfirmDialog
        open={confirmDisconnect}
        busy={busy}
        title="Disconnect GitHub?"
        confirmLabel="Disconnect"
        confirmClass={btn.danger}
        onCancel={() => setConfirmDisconnect(false)}
        onConfirm={handleDisconnect}
      >
        <p>The bot stops using this GitHub account. To remove access completely, also uninstall “JTS PowerTool” in the GitHub account&apos;s settings.</p>
      </ConfirmDialog>
    </section>
  );
}
