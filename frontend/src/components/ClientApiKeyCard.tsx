"use client";

import { useCallback, useEffect, useState } from "react";
import { KeyRound, ShieldCheck, Receipt, Eye, EyeOff, Loader2, Trash2, CheckCircle2, AlertCircle } from "lucide-react";
import { fetchFolderApiKey, saveFolderApiKey, deleteFolderApiKey } from "@/lib/api";
import { FolderApiKeyStatus, formatLocalDateTime } from "@/lib/types";

interface ClientApiKeyCardProps {
  folderId: number | string;
  canEdit: boolean;
}

export function ClientApiKeyCard({ folderId, canEdit }: ClientApiKeyCardProps) {
  const [status, setStatus] = useState<FolderApiKeyStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [apiKey, setApiKey] = useState("");
  const [showKey, setShowKey] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStatus(await fetchFolderApiKey(folderId));
    } catch (err: any) {
      setFeedback({ type: "error", message: err?.message || "Failed to load API key status" });
    } finally {
      setLoading(false);
    }
  }, [folderId]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!apiKey.trim()) return;
    setSaving(true);
    setFeedback(null);
    try {
      const res = await saveFolderApiKey(folderId, apiKey.trim());
      setStatus(res);
      setApiKey("");
      setShowKey(false);
      setFeedback({ type: "success", message: res.message });
    } catch (err: any) {
      setFeedback({ type: "error", message: err?.message || "Failed to save API key" });
    } finally {
      setSaving(false);
    }
  }

  async function handleRemove() {
    if (!confirm("Remove your own Anthropic key? The JTS key will be used and usage will be billed to your organization.")) {
      return;
    }
    setSaving(true);
    setFeedback(null);
    try {
      const res = await deleteFolderApiKey(folderId);
      setStatus(res);
      setFeedback({ type: "success", message: res.message });
    } catch (err: any) {
      setFeedback({ type: "error", message: err?.message || "Failed to remove API key" });
    } finally {
      setSaving(false);
    }
  }

  const usingOwnKey = !!status?.configured;

  return (
    <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 border-b border-gray-100">
        <div className="flex items-center gap-2.5">
          <div className="h-9 w-9 rounded-xl bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA]">
            <KeyRound className="h-4 w-4" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-gray-800">Anthropic API Key &amp; Billing</h2>
            <p className="text-[11px] text-gray-500">
              Use your own Anthropic key, or use the JTS key and be billed for usage.
            </p>
          </div>
        </div>

        {!loading && status && (
          <span
            className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold border self-start sm:self-auto ${
              usingOwnKey
                ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                : "bg-amber-50 text-amber-700 border-amber-200"
            }`}
          >
            {usingOwnKey ? <ShieldCheck className="h-3.5 w-3.5" /> : <Receipt className="h-3.5 w-3.5" />}
            {usingOwnKey ? "Own key · Not billed" : "JTS key · Billed"}
          </span>
        )}
      </div>

      {loading ? (
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <Loader2 className="h-4 w-4 animate-spin text-[#088ADA]" />
          <span>Loading key status...</span>
        </div>
      ) : (
        status && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-xs">
            <div
              className={`p-3 rounded-xl border ${
                usingOwnKey ? "border-emerald-300 bg-emerald-50/60" : "border-gray-200 bg-gray-50"
              }`}
            >
              <div className="font-semibold text-gray-800 flex items-center gap-1.5">
                <ShieldCheck className="h-3.5 w-3.5 text-emerald-600" />
                Your own key {usingOwnKey && <span className="text-emerald-700">(active)</span>}
              </div>
              <p className="text-gray-500 mt-1">
                Claude usage is charged by Anthropic to your own account. JTS does not bill you.
              </p>
              {usingOwnKey && (
                <p className="text-gray-600 mt-2 font-mono text-[11px]">
                  Key {status.key_hint} · added by {status.updated_by || "-"}
                  {status.updated_at ? ` · ${formatLocalDateTime(status.updated_at)}` : ""}
                </p>
              )}
            </div>
            <div
              className={`p-3 rounded-xl border ${
                !usingOwnKey ? "border-amber-300 bg-amber-50/60" : "border-gray-200 bg-gray-50"
              }`}
            >
              <div className="font-semibold text-gray-800 flex items-center gap-1.5">
                <Receipt className="h-3.5 w-3.5 text-amber-600" />
                JTS key (default) {!usingOwnKey && <span className="text-amber-700">(active)</span>}
              </div>
              <p className="text-gray-500 mt-1">
                Claude usage runs on the JTS key and is tracked and billed to your organization.
              </p>
            </div>
          </div>
        )
      )}

      {feedback && (
        <div
          className={`flex items-start gap-2 p-3 rounded-xl border text-xs ${
            feedback.type === "success"
              ? "bg-emerald-50 border-emerald-200 text-emerald-700"
              : "bg-rose-50 border-rose-200 text-rose-700"
          }`}
        >
          {feedback.type === "success" ? (
            <CheckCircle2 className="h-4 w-4 shrink-0 mt-0.5" />
          ) : (
            <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
          )}
          <span>{feedback.message}</span>
        </div>
      )}

      {canEdit && !loading && (
        <form onSubmit={handleSave} autoComplete="off" className="flex flex-col sm:flex-row gap-2">
          <div className="relative flex-1">
            <input
              type={showKey ? "text" : "password"}
              autoComplete="new-password"
              data-lpignore="true"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={usingOwnKey ? "Paste a new key to replace (sk-ant-...)" : "Paste your Anthropic API key (sk-ant-...)"}
              className="w-full pl-3.5 pr-10 py-2 bg-white border border-gray-300 rounded-xl text-xs text-gray-800 placeholder-gray-400 font-mono focus:outline-none focus:border-[#088ADA] focus:ring-1 focus:ring-[#088ADA]"
            />
            <button
              type="button"
              onClick={() => setShowKey(!showKey)}
              className="absolute right-3 top-2 text-gray-400 hover:text-gray-600"
              title={showKey ? "Hide key" : "Show key"}
            >
              {showKey ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
          <button
            type="submit"
            disabled={saving || !apiKey.trim()}
            className="flex items-center justify-center gap-1.5 px-4 py-2 rounded-xl bg-[#088ADA] hover:bg-[#0778bd] text-white text-xs font-semibold transition disabled:opacity-50"
          >
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <KeyRound className="h-3.5 w-3.5" />}
            <span>{usingOwnKey ? "Replace Key" : "Use My Own Key"}</span>
          </button>
          {usingOwnKey && (
            <button
              type="button"
              onClick={handleRemove}
              disabled={saving}
              className="flex items-center justify-center gap-1.5 px-4 py-2 rounded-xl bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200 text-xs font-semibold transition disabled:opacity-50"
            >
              <Trash2 className="h-3.5 w-3.5" />
              <span>Remove Key</span>
            </button>
          )}
        </form>
      )}

      {!canEdit && !loading && (
        <p className="text-[11px] text-gray-400">Only your Client Admin can add or remove the API key.</p>
      )}
    </div>
  );
}
