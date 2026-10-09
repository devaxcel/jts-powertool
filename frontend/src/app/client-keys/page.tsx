"use client";

import { useCallback, useEffect, useRef, useState } from "react";
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
import { JiraConnectionCard } from "@/components/JiraConnectionCard";
import { WordPressConnectionCard } from "@/components/WordPressConnectionCard";
import { Alert, Badge, ConfirmDialog, EmptyState, LoadingState, PageHeader, Section, Tabs, btn, inputClass, tbl } from "@/components/ui";

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
  const [tab, setTab] = useState<"connections" | "keys">("connections");

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

  // Opened from Slack ("Add key securely"): prefill the key name / channel and jump to the form.
  const [fromSlack, setFromSlack] = useState(false);
  const valueRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    const q = new URLSearchParams(window.location.search);
    if (q.get("add") === "1") {
      setFromSlack(true);
      setTab("keys");
      const n = (q.get("name") || "").toUpperCase().replace(/[^A-Z0-9_]+/g, "_").slice(0, 50);
      if (n) setKeyName(n);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  // Preselect the channel from the Slack link, once the client's channels are known.
  const channelParamApplied = useRef(false);
  useEffect(() => {
    if (!data || channelParamApplied.current) return;
    channelParamApplied.current = true;
    const ch = new URLSearchParams(window.location.search).get("channel");
    setAppliesTo(ch && data.channels.some((c) => c.channel_id === ch) ? ch : "client");
  }, [data]);

  useEffect(() => {
    if (fromSlack && data?.can_edit) valueRef.current?.focus();
  }, [fromSlack, data?.can_edit]);

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
          <Tabs
            value={tab}
            onChange={setTab}
            tabs={[
              { id: "connections" as const, label: "Connections", icon: Plug },
              { id: "keys" as const, label: "API keys", count: keys.length, icon: KeyRound },
            ]}
          />

          {tab === "connections" && (
            <div className="space-y-5">
              <div className="grid grid-cols-1 xl:grid-cols-2 gap-5 items-start">
                <GithubConnectionCard folderId={data.folder.id} canEdit={canEdit} />
                <JiraConnectionCard folderId={data.folder.id} canEdit={canEdit} />
              </div>
              <WordPressConnectionCard folderId={data.folder.id} canEdit={canEdit} />
            </div>
          )}

          {tab === "keys" && (
            <div className="space-y-5">
              {fromSlack && !canEdit && (
                <Alert type="warning">Only your Client Admin can add keys. Ask them to open the link from Slack, or sign in with a Client Admin account.</Alert>
              )}

              {canEdit ? (
                <Section
                  icon={Lock}
                  title="Add a key"
                  description="The value is stored securely in AWS and can't be viewed again after saving. Use the same name to replace an existing key. Name it ANTHROPIC_API_KEY to use your own Claude account for the assistant's replies."
                  className={fromSlack ? "border-[#088ADA] ring-2 ring-[#088ADA]/20" : ""}
                >
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
                      ref={valueRef}
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
                </Section>
              ) : (
                <Alert type="info">You can see which keys are saved. Only your Client Admin can add or remove them.</Alert>
              )}

              <Section title={`Saved keys (${keys.length})`} description="Only the names are shown. Values are never displayed." bodyClassName="p-0">
                {keys.length === 0 ? (
                  <div className="p-5">
                    <EmptyState icon={Lock} title="No keys saved yet" description="Add your first API key or token using the form above." />
                  </div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className={tbl.table}>
                      <thead className={tbl.head}>
                        <tr>
                          <th className={tbl.th}>Key name</th>
                          <th className={tbl.th}>Applies to</th>
                          <th className={tbl.th}>Added by</th>
                          <th className={tbl.th}>Last updated</th>
                          {canEdit && <th className={`${tbl.th} text-right`}>Actions</th>}
                        </tr>
                      </thead>
                      <tbody>
                        {keys.map((k) => (
                          <tr key={`${k.applies_to}-${k.channel_id || ""}-${k.provider}`} className={tbl.row}>
                            <td className={tbl.td}>
                              <div className="flex items-center gap-2.5">
                                <div className="h-8 w-8 rounded-lg bg-sky-50 border border-sky-100 flex items-center justify-center text-[#088ADA] shrink-0">
                                  <KeyRound className="h-3.5 w-3.5" />
                                </div>
                                <div className="min-w-0">
                                  <div className="text-sm font-semibold text-gray-900 truncate">{k.label}</div>
                                  {k.key_hint && <div className="font-mono text-[11px] text-gray-500">{k.key_hint}</div>}
                                </div>
                                {k.status === "failing" && <Badge tone="rose">Failing</Badge>}
                              </div>
                            </td>
                            <td className={tbl.td}>
                              {k.applies_to === "client" ? <Badge tone="blue">Whole client</Badge> : <Badge tone="gray">Only {k.channel_name}</Badge>}
                            </td>
                            <td className={tbl.td}>{k.updated_by || "-"}</td>
                            <td className={`${tbl.td} text-gray-500 whitespace-nowrap`}>{k.updated_at ? formatLocalDateTime(k.updated_at) : "-"}</td>
                            {canEdit && (
                              <td className={`${tbl.td} text-right`}>
                                <button
                                  onClick={() => setRemoving(k)}
                                  className="p-1.5 text-gray-400 hover:text-rose-600 hover:bg-rose-50 rounded-lg transition"
                                  title="Delete key"
                                  aria-label={`Delete ${k.label}`}
                                >
                                  <Trash2 className="h-4 w-4" />
                                </button>
                              </td>
                            )}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Section>
            </div>
          )}
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
