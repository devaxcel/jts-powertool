"use client";

import { useCallback, useEffect, useState } from "react";
import { Plug, Plus, Trash2, RefreshCw, Loader2, Eye, EyeOff, Lock, KeyRound, ShieldCheck, Building2 } from "lucide-react";
import {
  ClientKeysData,
  ClientKeyItem,
  fetchClientKeys,
  saveClientKey,
  deleteClientKey,
  saveChannelKey,
  deleteChannelKey,
  fetchFolders,
} from "@/lib/api";
import { ChannelFolder, formatLocalDateTime } from "@/lib/types";
import { GithubConnectionCard } from "@/components/GithubConnectionCard";
import { Alert, ConfirmDialog, EmptyState, LoadingState, PageHeader, btn, inputClass } from "@/components/ui";

export default function ClientKeysPage() {
  const [role, setRole] = useState("jts_admin");
  const [folders, setFolders] = useState<ChannelFolder[]>([]);
  const [folderId, setFolderId] = useState<number | null>(null);
  const [data, setData] = useState<ClientKeysData | null>(null);
  const [loading, setLoading] = useState(true);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  // Form state (same fields as the JTS Admin "API keys" page, plus where the key applies)
  const [keyName, setKeyName] = useState("");
  const [keyValue, setKeyValue] = useState("");
  const [appliesTo, setAppliesTo] = useState("client");
  const [showValue, setShowValue] = useState(false);
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState<ClientKeyItem | null>(null);
  const [busy, setBusy] = useState(false);

  const isJts = role === "jts_admin";

  useEffect(() => {
    try {
      const u = JSON.parse(sessionStorage.getItem("jts_user") || "{}");
      const r = sessionStorage.getItem("jts_simulated_role") || u.role || "jts_admin";
      setRole(r);
      const fromUrl = Number(new URLSearchParams(window.location.search).get("folder"));
      if (r === "jts_admin") {
        fetchFolders()
          .then((list) => {
            setFolders(list);
            setFolderId(fromUrl || list[0]?.id || null);
            if (!fromUrl && !list.length) setLoading(false);
          })
          .catch((e) => {
            setFeedback({ type: "error", message: e.message });
            setLoading(false);
          });
      } else {
        setFolderId(-1); // the server always uses the client user's own client
      }
    } catch {
      setFolderId(-1);
    }
  }, []);

  const load = useCallback(async () => {
    if (folderId === null) return;
    setLoading(true);
    try {
      setData(await fetchClientKeys(folderId > 0 ? folderId : undefined));
    } catch (e: any) {
      setFeedback({ type: "error", message: e?.message || "We couldn't load the keys." });
    } finally {
      setLoading(false);
    }
  }, [folderId]);

  useEffect(() => {
    setAppliesTo("client");
    load();
  }, [load]);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    if (!data) return;
    setSaving(true);
    try {
      const body = { folder_id: data.folder.id, name: keyName.trim(), value: keyValue.trim() };
      const res = appliesTo === "client" ? await saveClientKey(body) : await saveChannelKey({ ...body, channel_id: appliesTo });
      setFeedback({ type: "success", message: res.message });
      setKeyName("");
      setKeyValue("");
      setShowValue(false);
      await load();
    } catch (err: any) {
      setFeedback({ type: "error", message: err?.message || "We couldn't save the key." });
    } finally {
      setSaving(false);
    }
  }

  async function confirmRemove() {
    if (!removing || !data) return;
    setBusy(true);
    try {
      const res =
        removing.applies_to === "client"
          ? await deleteClientKey(removing.provider, data.folder.id)
          : await deleteChannelKey(removing.channel_id || "", removing.provider, data.folder.id);
      setFeedback({ type: "success", message: res.message });
      setRemoving(null);
      await load();
    } catch (err: any) {
      setFeedback({ type: "error", message: err?.message || "We couldn't remove the key." });
    } finally {
      setBusy(false);
    }
  }

  const canEdit = Boolean(data?.can_edit);
  const keys = data?.keys || [];

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <PageHeader
        icon={Plug}
        title="Keys & Connections"
        description="Your own API keys and connections. Keys are stored encrypted and never shown again."
        badge={
          <span className="px-2 py-0.5 rounded-full text-[11px] font-medium bg-emerald-50 text-emerald-700 border border-emerald-200 inline-flex items-center gap-1">
            <ShieldCheck className="h-3 w-3" />
            Stored encrypted
          </span>
        }
        actions={
          <>
            {isJts && folders.length > 0 && (
              <select
                className="px-3 py-2 bg-white border border-gray-300 rounded-xl text-sm text-gray-800"
                value={folderId ?? ""}
                onChange={(e) => setFolderId(Number(e.target.value))}
                aria-label="Client"
              >
                {folders.map((f) => (
                  <option key={f.id} value={f.id}>
                    {f.name}
                  </option>
                ))}
              </select>
            )}
            <button onClick={load} disabled={loading} className={btn.secondary}>
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              <span>Refresh</span>
            </button>
          </>
        }
      />

      {feedback && (
        <Alert type={feedback.type} onClose={() => setFeedback(null)}>
          {feedback.message}
        </Alert>
      )}

      {loading && !data ? (
        <LoadingState label="Loading keys..." />
      ) : !data ? (
        !feedback && <EmptyState icon={Building2} title="No client selected" description="Create a client first in Clients & Channels." />
      ) : (
        <>
          <GithubConnectionCard folderId={data.folder.id} canEdit={canEdit} />

          {canEdit ? (
            <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm space-y-4">
              <div className="flex items-center gap-2 pb-3 border-b border-gray-100">
                <div className="h-8 w-8 rounded-lg bg-[#088ADA]/10 flex items-center justify-center text-[#088ADA]">
                  <Lock className="h-4 w-4" />
                </div>
                <div>
                  <h2 className="text-sm font-semibold text-gray-800">Add a key</h2>
                  <p className="text-xs text-gray-500">
                    The value is stored securely in AWS and can&apos;t be viewed again after saving. Use the same name to replace an existing key. Name it
                    ANTHROPIC_API_KEY to use your own Claude account for the assistant&apos;s replies.
                  </p>
                </div>
              </div>

              <form onSubmit={handleSave} autoComplete="off" data-lpignore="true" className="grid grid-cols-1 sm:grid-cols-12 gap-4 items-end">
                <input type="text" name="fake_username_remembered" tabIndex={-1} className="hidden" aria-hidden="true" autoComplete="off" />
                <input type="password" name="fake_password_remembered" tabIndex={-1} className="hidden" aria-hidden="true" autoComplete="off" />

                <div className="sm:col-span-3">
                  <label className="block text-xs font-medium text-gray-700 mb-1.5">
                    Key Name <span className="text-rose-500">*</span>
                  </label>
                  <input
                    type="text"
                    name="client_key_name_field"
                    autoComplete="off"
                    data-lpignore="true"
                    data-form-type="other"
                    required
                    value={keyName}
                    maxLength={50}
                    onChange={(e) => setKeyName(e.target.value.toUpperCase().replace(/\s+/g, "_"))}
                    placeholder="e.g. OPENAI_API_KEY"
                    className="w-full px-3.5 py-2.5 bg-white border border-gray-300 rounded-xl text-gray-800 text-xs placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:ring-1 focus:ring-[#088ADA] transition font-mono"
                  />
                </div>

                <div className="sm:col-span-3">
                  <label className="block text-xs font-medium text-gray-700 mb-1.5">Applies to</label>
                  <select className={`${inputClass} text-xs`} value={appliesTo} onChange={(e) => setAppliesTo(e.target.value)}>
                    <option value="client">Whole client (all channels)</option>
                    {data.channels.map((c) => (
                      <option key={c.channel_id} value={c.channel_id}>
                        Only {c.channel_name}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="sm:col-span-4">
                  <label className="block text-xs font-medium text-gray-700 mb-1.5">
                    Key Value (Secret) <span className="text-rose-500">*</span>
                  </label>
                  <div className="relative">
                    <input
                      type={showValue ? "text" : "password"}
                      name="client_key_value_field"
                      autoComplete="new-password"
                      data-lpignore="true"
                      data-form-type="other"
                      required
                      value={keyValue}
                      onChange={(e) => setKeyValue(e.target.value)}
                      placeholder="Paste the key"
                      className="w-full pl-3.5 pr-10 py-2.5 bg-white border border-gray-300 rounded-xl text-gray-800 text-xs placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:ring-1 focus:ring-[#088ADA] transition font-mono"
                    />
                    <button
                      type="button"
                      onClick={() => setShowValue(!showValue)}
                      className="absolute right-3 top-3 text-gray-400 hover:text-gray-600 transition"
                      aria-label={showValue ? "Hide key" : "Show key"}
                    >
                      {showValue ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
                    </button>
                  </div>
                </div>

                <div className="sm:col-span-2">
                  <button
                    type="submit"
                    disabled={saving || !keyName.trim() || !keyValue.trim()}
                    className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-[#088ADA] hover:bg-[#0778bd] text-white text-xs font-semibold rounded-xl shadow-lg shadow-sky-600/20 transition disabled:opacity-50"
                  >
                    {saving ? (
                      <>
                        <Loader2 className="h-4 w-4 animate-spin" />
                        <span>Saving...</span>
                      </>
                    ) : (
                      <>
                        <Plus className="h-4 w-4" />
                        <span>Save key</span>
                      </>
                    )}
                  </button>
                </div>
              </form>
            </div>
          ) : (
            <Alert type="info">You can see which keys are saved. Only your Client Admin can add or remove them.</Alert>
          )}

          <div className="space-y-3">
            <div>
              <h2 className="text-sm font-semibold text-gray-800">Saved keys ({keys.length})</h2>
              <p className="text-xs text-gray-500">Only the names are shown. Values are never displayed.</p>
            </div>

            {keys.length === 0 ? (
              <EmptyState icon={Lock} title="No keys saved yet" description="Add your first API key or token using the form above." />
            ) : (
              <div className="overflow-x-auto rounded-xl border border-gray-200 shadow-sm">
                <table className="w-full text-left border-collapse">
                  <thead className="bg-[#088ADA] text-white text-xs uppercase tracking-wider">
                    <tr className="bg-[#088ADA]">
                      <th className="p-3 font-semibold bg-[#088ADA] text-white">Key name</th>
                      <th className="p-3 font-semibold bg-[#088ADA] text-white">Applies to</th>
                      <th className="p-3 font-semibold bg-[#088ADA] text-white">Added by</th>
                      <th className="p-3 font-semibold text-center bg-[#088ADA] text-white">Last updated</th>
                      {canEdit && <th className="p-3 font-semibold text-right bg-[#088ADA] text-white">Actions</th>}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-gray-200">
                    {keys.map((k, idx) => (
                      <tr key={`${k.applies_to}-${k.channel_id || ""}-${k.provider}`} className={`transition hover:bg-gray-200 ${idx % 2 === 0 ? "bg-white" : "bg-[#ededed]"}`}>
                        <td className="p-3">
                          <div className="flex items-center gap-2.5">
                            <div className="h-7 w-7 rounded-lg bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA] shrink-0">
                              <KeyRound className="h-3.5 w-3.5" />
                            </div>
                            <div>
                              <span className="text-sm font-semibold text-gray-800">{k.label}</span>
                              {k.key_hint && <span className="ml-2 font-mono text-xs text-gray-500">{k.key_hint}</span>}
                              {k.status === "failing" && (
                                <span className="ml-2 text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-rose-50 text-rose-700 border border-rose-200">Failing</span>
                              )}
                            </div>
                          </div>
                        </td>
                        <td className="p-3 text-xs text-gray-700">
                          {k.applies_to === "client" ? "Whole client" : `Only ${k.channel_name}`}
                        </td>
                        <td className="p-3 text-xs font-medium text-gray-700">{k.updated_by || "-"}</td>
                        <td className="p-3 text-center text-xs text-gray-600">{k.updated_at ? formatLocalDateTime(k.updated_at) : "-"}</td>
                        {canEdit && (
                          <td className="p-3 text-right">
                            <button
                              onClick={() => setRemoving(k)}
                              className="p-1.5 text-gray-500 hover:text-rose-600 hover:bg-rose-100 rounded-lg transition"
                              title="Delete key"
                              aria-label={`Delete ${k.label}`}
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}

      <ConfirmDialog
        open={Boolean(removing)}
        busy={busy}
        title={`Remove ${removing?.label}?`}
        confirmLabel="Remove key"
        confirmClass={btn.danger}
        onCancel={() => setRemoving(null)}
        onConfirm={confirmRemove}
      >
        <p>
          {removing?.provider === "anthropic"
            ? "The assistant will use the JTS key again, and replies will be billed."
            : "The key is deleted from secure storage."}
        </p>
      </ConfirmDialog>
    </div>
  );
}
