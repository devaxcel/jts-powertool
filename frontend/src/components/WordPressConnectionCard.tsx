"use client";

import { useCallback, useEffect, useState } from "react";
import { Download, Eye, EyeOff, Globe, Link2, Loader2, RefreshCw, Unlink } from "lucide-react";
import {
  WordPressStatus,
  checkWordPress,
  connectWordPress,
  disconnectWordPress,
  downloadWordPressConnector,
  fetchWordPressStatus,
} from "@/lib/api";
import { formatLocalDateTime } from "@/lib/types";
import { Alert, Badge, ConfirmDialog, Section, btn, inputClass } from "@/components/ui";

const EDITOR_NAMES: Record<string, string> = {
  gutenberg: "Gutenberg",
  classic: "Classic editor",
  elementor: "Elementor",
  divi: "Divi",
  wpbakery: "WPBakery",
  other_builder: "Other builder",
  empty: "Empty",
};
// How well the assistant can change each kind of page.
const EDITOR_SUPPORT: Record<string, "full" | "text" | "read"> = {
  gutenberg: "full",
  classic: "full",
  elementor: "text",
  divi: "read",
  wpbakery: "read",
  other_builder: "read",
  empty: "full",
};

export function WordPressConnectionCard({ folderId, canEdit }: { folderId: number | string; canEdit: boolean }) {
  const id = Number(folderId);
  const [status, setStatus] = useState<WordPressStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error" | "info"; message: string } | null>(null);
  const [confirmDisconnect, setConfirmDisconnect] = useState(false);
  const [showForm, setShowForm] = useState(false);
  const [siteUrl, setSiteUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await fetchWordPressStatus(id));
    } catch (e: any) {
      setFeedback({ type: "error", message: e.message });
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleConnect(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFeedback(null);
    try {
      const res = await connectWordPress(id, { site_url: siteUrl.trim(), username: username.trim(), app_password: password });
      setPassword("");
      setShowForm(false);
      setFeedback({ type: "success", message: res.message });
      await load();
    } catch (err: any) {
      setFeedback({ type: "error", message: err.message });
    } finally {
      setBusy(false);
    }
  }

  async function handleCheck() {
    setBusy(true);
    setFeedback(null);
    try {
      await checkWordPress(id);
      setFeedback({ type: "success", message: "The site answered and the pages were counted again." });
      await load();
    } catch (err: any) {
      setFeedback({ type: "error", message: err.message });
      await load();
    } finally {
      setBusy(false);
    }
  }

  async function handleDisconnect() {
    setBusy(true);
    try {
      const res = await disconnectWordPress(id);
      setFeedback({ type: "success", message: res.message });
      setConfirmDisconnect(false);
      await load();
    } catch (err: any) {
      setFeedback({ type: "error", message: err.message });
    } finally {
      setBusy(false);
    }
  }

  async function handleDownload() {
    try {
      await downloadWordPressConnector();
    } catch (err: any) {
      setFeedback({ type: "error", message: err.message });
    }
  }

  const conn = status?.connection;
  const connected = Boolean(status?.connected && conn);
  const editors = status?.editors || {};
  const sampled = editors.sampled || 0;
  const hasElementor = (editors.elementor || 0) > 0;
  const needsPlugin = hasElementor && status?.connector && !status.connector.installed;

  return (
    <Section
      icon={Globe}
      title={
        <span className="inline-flex items-center gap-2">
          WordPress
          {!loading && <Badge tone={connected ? "green" : conn ? "rose" : "gray"}>{connected ? "Connected" : conn ? "Needs reconnecting" : "Not connected"}</Badge>}
        </span>
      }
      description="The bot can read this site's pages and posts, and change them, create drafts, publish or trash them. Every change waits for approval and WordPress keeps earlier versions under Revisions."
      actions={
        <>
          <button onClick={load} className={btn.secondary} disabled={loading} aria-label="Refresh WordPress status">
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          </button>
          {canEdit && !showForm && (
            <button onClick={() => setShowForm(true)} className={btn.primary}>
              <Link2 className="h-3.5 w-3.5" />
              {conn ? "Change connection" : "Connect WordPress"}
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

      {showForm && canEdit && (
        <form onSubmit={handleConnect} className="space-y-3 p-4 rounded-xl border border-gray-200 bg-gray-50/60">
          <ol className="text-xs text-gray-600 space-y-1 list-decimal list-inside">
            <li>
              In the WordPress admin open <strong>Users → Profile</strong> and scroll to <strong>Application Passwords</strong>.
            </li>
            <li>
              Type a name such as <em>JTS PowerTool</em> and click <strong>Add New Application Password</strong>. Copy the password WordPress shows (it is shown once).
            </li>
            <li>Enter the site address, the WordPress username and that password below. Use a user with the Editor role (or Administrator).</li>
          </ol>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <label className="block space-y-1 sm:col-span-2">
              <span className="text-xs font-semibold text-gray-700">Site address</span>
              <input value={siteUrl} onChange={(e) => setSiteUrl(e.target.value)} required placeholder="https://www.example.com" className={inputClass} autoComplete="off" />
            </label>
            <label className="block space-y-1">
              <span className="text-xs font-semibold text-gray-700">WordPress username</span>
              <input value={username} onChange={(e) => setUsername(e.target.value)} required placeholder="e.g. sara" className={inputClass} autoComplete="off" />
            </label>
            <label className="block space-y-1">
              <span className="text-xs font-semibold text-gray-700">Application password</span>
              <div className="relative">
                <input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  placeholder="xxxx xxxx xxxx xxxx xxxx xxxx"
                  className={`${inputClass} pr-10 font-mono`}
                  autoComplete="new-password"
                />
                <button type="button" onClick={() => setShowPassword((v) => !v)} aria-label={showPassword ? "Hide password" : "Show password"} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600">
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </label>
          </div>
          <p className="text-[11px] text-gray-500">The password is stored encrypted and is never shown again. It is not your normal login password, and you can revoke it in WordPress at any time.</p>
          <div className="flex justify-end gap-2">
            <button type="button" onClick={() => { setShowForm(false); setPassword(""); }} className={btn.secondary} disabled={busy}>
              Cancel
            </button>
            <button type="submit" className={btn.primary} disabled={busy}>
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Link2 className="h-3.5 w-3.5" />}
              Connect and test
            </button>
          </div>
        </form>
      )}

      {loading && !status ? (
        <p className="text-xs text-gray-500 flex items-center gap-2">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Checking WordPress…
        </p>
      ) : conn && !connected ? (
        <Alert type="warning" title="WordPress no longer accepts the saved password">
          It may have been revoked. Click “Change connection” and enter a new application password.
        </Alert>
      ) : connected && conn ? (
        <div className="space-y-4">
          {status?.error && <Alert type="warning">{status.error}</Alert>}
          <dl className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
            <div className="min-w-0">
              <dt className="text-gray-500">Site</dt>
              <dd className="font-semibold text-gray-900 mt-0.5 truncate">{conn.site_name || conn.site_url}</dd>
              <dd className="text-gray-500 truncate">{conn.site_url}</dd>
            </div>
            <div>
              <dt className="text-gray-500">Works as</dt>
              <dd className="font-semibold text-gray-900 mt-0.5">{conn.wp_user_name || conn.wp_user}</dd>
              <dd className="text-gray-500">{conn.wp_roles || "—"}</dd>
            </div>
            <div>
              <dt className="text-gray-500">Connected</dt>
              <dd className="text-gray-800 mt-0.5" suppressHydrationWarning>
                {conn.updated_at ? formatLocalDateTime(conn.updated_at) : "—"}
              </dd>
            </div>
          </dl>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-semibold text-gray-700">Editors found on the site{sampled ? ` (${sampled} recent pages and posts)` : ""}</h4>
              {canEdit && (
                <button onClick={handleCheck} disabled={busy} className="text-xs font-semibold text-[#088ADA] hover:underline disabled:opacity-50">
                  Check again
                </button>
              )}
            </div>
            {sampled ? (
              <ul className="flex flex-wrap gap-2">
                {Object.entries(editors)
                  .filter(([k]) => k !== "sampled")
                  .map(([k, n]) => {
                    const support = EDITOR_SUPPORT[k] || "read";
                    return (
                      <li key={k} className="inline-flex items-center gap-1.5 text-[11px] px-2.5 py-1 rounded-full border border-gray-200 bg-white">
                        <span className="font-semibold text-gray-800">{EDITOR_NAMES[k] || k}</span>
                        <span className="text-gray-500">{n}</span>
                        <Badge tone={support === "full" ? "green" : support === "text" ? "blue" : "gray"}>
                          {support === "full" ? "full editing" : support === "text" ? "text only" : "read only"}
                        </Badge>
                      </li>
                    );
                  })}
              </ul>
            ) : (
              <p className="text-xs text-gray-500">No pages were counted yet. Click “Check again”.</p>
            )}
          </div>

          {(hasElementor || status?.connector?.installed) && (
            <div className={`p-3 rounded-xl border text-xs space-y-2 ${needsPlugin ? "border-amber-200 bg-amber-50" : "border-gray-200 bg-gray-50"}`}>
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <span className="font-semibold text-gray-800">
                  Elementor text editing:{" "}
                  {status?.connector?.installed ? (
                    <span className="text-emerald-700">plugin installed (v{status.connector.version})</span>
                  ) : (
                    <span className="text-amber-700">plugin needed</span>
                  )}
                </span>
                <button onClick={handleDownload} className={btn.secondary}>
                  <Download className="h-3.5 w-3.5" />
                  Download connector plugin
                </button>
              </div>
              <p className="text-gray-600">
                Elementor keeps its text in hidden page data, so a small free plugin must be on the site to read and change it. In WordPress open{" "}
                <strong>Plugins → Add New → Upload Plugin</strong>, choose the downloaded zip, then click Install and Activate. It only touches text (headings, paragraphs, buttons) after an approval, never layout or styles. Then click “Check again”.
              </p>
            </div>
          )}

          {canEdit && (
            <div className="flex flex-wrap items-center justify-between gap-2 pt-3 border-t border-gray-100">
              <p className="text-[11px] text-gray-500">To stop access completely, also revoke the application password in WordPress (Users → Profile).</p>
              <button onClick={() => setConfirmDisconnect(true)} className={btn.dangerSoft} disabled={busy}>
                <Unlink className="h-3.5 w-3.5" /> Disconnect
              </button>
            </div>
          )}
        </div>
      ) : (
        !showForm && (
          <p className="text-sm text-gray-600">
            Not connected yet. {canEdit ? "Click “Connect WordPress” and follow the three steps." : "Ask your Client Admin to connect the site."}
          </p>
        )
      )}

      <ConfirmDialog open={confirmDisconnect} busy={busy} title="Disconnect WordPress?" confirmLabel="Disconnect" confirmClass={btn.danger} onCancel={() => setConfirmDisconnect(false)} onConfirm={handleDisconnect}>
        <p>The bot stops reading and changing this site. Nothing on the site is removed. Also revoke the application password in WordPress to be sure.</p>
      </ConfirmDialog>
    </Section>
  );
}
