"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Plug, Plus, Trash2, RefreshCw, Loader2, Eye, EyeOff, Hash, Building2, Bot, Clock } from "lucide-react";
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
import { ClientApiKeyCard } from "@/components/ClientApiKeyCard";
import { GithubConnectionCard } from "@/components/GithubConnectionCard";
import { Alert, ConfirmDialog, EmptyState, LoadingState, PageHeader, btn, inputClass } from "@/components/ui";

type Target = { scope: "client" } | { scope: "channel"; channelId: string; channelName: string };
type Pending = { provider: string; label: string; target: Target } | null;

function KeyRow({
  item,
  canEdit,
  usedByBot,
  onReplace,
  onRemove,
}: {
  item: ClientKeyItem;
  canEdit: boolean;
  usedByBot: boolean;
  onReplace: () => void;
  onRemove: () => void;
}) {
  return (
    <li className="py-3 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
      <div className="min-w-0">
        <p className="text-sm font-semibold text-gray-800 flex items-center gap-2 flex-wrap">
          {item.label}
          {item.key_hint && <span className="font-mono text-xs text-gray-500">{item.key_hint}</span>}
          {item.status === "failing" ? (
            <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-rose-50 text-rose-700 border border-rose-200">Failing</span>
          ) : usedByBot ? (
            <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 inline-flex items-center gap-1">
              <Bot className="h-3 w-3" /> In use by the assistant
            </span>
          ) : (
            <span className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-gray-100 text-gray-600 border border-gray-200 inline-flex items-center gap-1">
              <Clock className="h-3 w-3" /> Stored for upcoming tools
            </span>
          )}
        </p>
        <p className="text-xs text-gray-500">
          {item.updated_by ? `Saved by ${item.updated_by}` : "Saved"}
          {item.updated_at ? ` · ${formatLocalDateTime(item.updated_at)}` : ""}
        </p>
      </div>
      {canEdit && (
        <div className="flex items-center gap-2 shrink-0">
          <button onClick={onReplace} className={btn.secondary}>
            Replace
          </button>
          <button onClick={onRemove} className={btn.dangerSoft}>
            <Trash2 className="h-3.5 w-3.5" /> Remove
          </button>
        </div>
      )}
    </li>
  );
}

function AddKeyForm({
  data,
  initial,
  onSaved,
  onCancel,
}: {
  data: ClientKeysData;
  initial?: { provider: string; target: Target };
  onSaved: (message: string) => void;
  onCancel: () => void;
}) {
  const presetCustom = initial?.provider.startsWith("custom_");
  const [provider, setProvider] = useState(presetCustom ? "custom" : initial?.provider || "anthropic");
  const [customName, setCustomName] = useState(presetCustom ? initial!.provider.slice(7).replace(/_/g, " ") : "");
  const [targetKey, setTargetKey] = useState(
    initial?.target.scope === "channel" ? `channel:${initial.target.channelId}` : "client"
  );
  const [value, setValue] = useState("");
  const [show, setShow] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const info = data.providers.find((p) => p.id === provider);
  const groups = useMemo(() => Array.from(new Set(data.providers.map((p) => p.group))), [data.providers]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const body = { folder_id: data.folder.id, provider, custom_name: provider === "custom" ? customName : undefined, value };
      const res = targetKey === "client" ? await saveClientKey(body) : await saveChannelKey({ ...body, channel_id: targetKey.slice(8) });
      setValue("");
      onSaved(res.message);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="rounded-2xl border border-sky-200 bg-sky-50/40 p-4 space-y-3" autoComplete="off">
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <label className="block space-y-1">
          <span className="text-xs font-semibold text-gray-700">Key type</span>
          <select className={inputClass} value={provider} onChange={(e) => setProvider(e.target.value)} disabled={Boolean(initial)}>
            {groups.map((g) => (
              <optgroup key={g} label={g}>
                {data.providers
                  .filter((p) => p.group === g)
                  .map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.label}
                    </option>
                  ))}
              </optgroup>
            ))}
          </select>
        </label>
        {provider === "custom" && (
          <label className="block space-y-1">
            <span className="text-xs font-semibold text-gray-700">Name</span>
            <input className={inputClass} value={customName} maxLength={40} onChange={(e) => setCustomName(e.target.value)} placeholder="e.g. CRM key" disabled={Boolean(initial)} />
          </label>
        )}
        <label className="block space-y-1">
          <span className="text-xs font-semibold text-gray-700">Applies to</span>
          <select className={inputClass} value={targetKey} onChange={(e) => setTargetKey(e.target.value)} disabled={Boolean(initial)}>
            <option value="client">Whole client (all channels)</option>
            {data.channels.map((c) => (
              <option key={c.channel_id} value={`channel:${c.channel_id}`}>
                Only {c.channel_name}
              </option>
            ))}
          </select>
        </label>
      </div>
      <label className="block space-y-1">
        <span className="text-xs font-semibold text-gray-700">Key value</span>
        <div className="relative">
          <input
            type={show ? "text" : "password"}
            className={`${inputClass} pr-10 font-mono`}
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder={info?.hint || "Paste the key"}
            autoComplete="new-password"
            spellCheck={false}
          />
          <button type="button" onClick={() => setShow(!show)} className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600" aria-label={show ? "Hide key" : "Show key"}>
            {show ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        </div>
        <span className="block text-[11px] text-gray-500">
          Stored encrypted in AWS Secrets Manager. It can't be viewed again, only replaced or removed.
          {info && !info.used_by_bot && " The assistant doesn't use this key type yet; it's kept ready for upcoming tools."}
        </span>
      </label>
      {error && <Alert type="error">{error}</Alert>}
      <div className="flex items-center justify-end gap-2">
        <button type="button" onClick={onCancel} className={btn.secondary}>
          Cancel
        </button>
        <button type="submit" disabled={saving || !value.trim() || (provider === "custom" && !customName.trim())} className={btn.primary}>
          {saving && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
          {initial ? "Replace key" : "Save key"}
        </button>
      </div>
    </form>
  );
}

export default function ClientKeysPage() {
  const [role, setRole] = useState("jts_admin");
  const [folders, setFolders] = useState<ChannelFolder[]>([]);
  const [folderId, setFolderId] = useState<number | null>(null);
  const [data, setData] = useState<ClientKeysData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [replacing, setReplacing] = useState<{ provider: string; target: Target } | null>(null);
  const [removing, setRemoving] = useState<Pending>(null);
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
            setError(e.message);
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
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [folderId]);

  useEffect(() => {
    load();
  }, [load]);

  const usedByBot = useCallback(
    (provider: string) => Boolean(data?.providers.find((p) => p.id === provider)?.used_by_bot),
    [data]
  );

  async function confirmRemove() {
    if (!removing || !data) return;
    setBusy(true);
    try {
      const res =
        removing.target.scope === "client"
          ? await deleteClientKey(removing.provider, data.folder.id)
          : await deleteChannelKey(removing.target.channelId, removing.provider, data.folder.id);
      setFeedback(res.message);
      setRemoving(null);
      await load();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const canEdit = Boolean(data?.can_edit);
  const channelKeyCount = data?.channels.reduce((n, c) => n + c.keys.length, 0) || 0;

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      <PageHeader
        icon={Plug}
        title="Keys & Connections"
        description="Your organization's own API keys and connections. Keys are stored encrypted, never shown again, and only used for your Slack channels."
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
            <button onClick={load} className={btn.secondary} disabled={loading} aria-label="Refresh">
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            </button>
          </>
        }
      />

      {feedback && (
        <Alert type="success" onClose={() => setFeedback(null)}>
          {feedback}
        </Alert>
      )}
      {error && (
        <Alert type="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {loading && !data ? (
        <LoadingState label="Loading keys..." />
      ) : !data ? (
        !error && <EmptyState icon={Building2} title="No client selected" description="Create a client first in Clients & Channels." />
      ) : (
        <>
          {!canEdit && (
            <Alert type="info">You can see which keys are set up. Only your Client Admin can add, replace or remove them.</Alert>
          )}

          <ClientApiKeyCard folderId={data.folder.id} canEdit={canEdit} />
          <GithubConnectionCard folderId={data.folder.id} canEdit={canEdit} />

          {/* Client-wide keys */}
          <section className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm space-y-4">
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div>
                <h2 className="text-sm font-semibold text-gray-800 flex items-center gap-2">
                  <Building2 className="h-4 w-4 text-[#088ADA]" /> Keys for all of {data.folder.name}
                </h2>
                <p className="text-xs text-gray-500 mt-0.5">Used in every one of your Slack channels unless a channel has its own key.</p>
              </div>
              {canEdit && !adding && (
                <button
                  onClick={() => {
                    setReplacing(null);
                    setAdding(true);
                  }}
                  className={btn.primary}
                >
                  <Plus className="h-3.5 w-3.5" /> Add key
                </button>
              )}
            </div>
            {adding && (
              <AddKeyForm
                data={data}
                onCancel={() => setAdding(false)}
                onSaved={(m) => {
                  setAdding(false);
                  setFeedback(m);
                  load();
                }}
              />
            )}
            {data.client_keys.length === 0 ? (
              <p className="text-xs text-gray-600">No client-wide keys yet.</p>
            ) : (
              <ul className="divide-y divide-gray-100">
                {data.client_keys.map((k) => (
                  <KeyRow
                    key={k.provider}
                    item={k}
                    canEdit={canEdit}
                    usedByBot={usedByBot(k.provider)}
                    onReplace={() => {
                      setAdding(false);
                      setReplacing({ provider: k.provider, target: { scope: "client" } });
                    }}
                    onRemove={() => setRemoving({ provider: k.provider, label: k.label, target: { scope: "client" } })}
                  />
                ))}
              </ul>
            )}
            {replacing && replacing.target.scope === "client" && (
              <AddKeyForm
                data={data}
                initial={replacing}
                onCancel={() => setReplacing(null)}
                onSaved={(m) => {
                  setReplacing(null);
                  setFeedback(m);
                  load();
                }}
              />
            )}
          </section>

          {/* Per-channel keys */}
          <section className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm space-y-4">
            <div>
              <h2 className="text-sm font-semibold text-gray-800 flex items-center gap-2">
                <Hash className="h-4 w-4 text-[#088ADA]" /> Keys for a single channel
              </h2>
              <p className="text-xs text-gray-500 mt-0.5">
                Override a client-wide key in one channel, e.g. a separate Anthropic key for one project. Add them with &ldquo;Add key&rdquo; above and choose the channel.
              </p>
            </div>
            {data.channels.length === 0 ? (
              <p className="text-xs text-gray-600">This client has no Slack channels yet.</p>
            ) : channelKeyCount === 0 ? (
              <p className="text-xs text-gray-600">No channel-specific keys yet.</p>
            ) : (
              <div className="space-y-4">
                {data.channels
                  .filter((c) => c.keys.length > 0)
                  .map((c) => (
                    <div key={c.channel_id}>
                      <p className="text-xs font-semibold text-gray-700 flex items-center gap-1">
                        <Hash className="h-3 w-3" />
                        {c.channel_name.replace(/^#/, "")}
                      </p>
                      <ul className="divide-y divide-gray-100">
                        {c.keys.map((k) => (
                          <KeyRow
                            key={`${c.channel_id}-${k.provider}`}
                            item={k}
                            canEdit={canEdit}
                            usedByBot={usedByBot(k.provider)}
                            onReplace={() => {
                              setAdding(false);
                              setReplacing({ provider: k.provider, target: { scope: "channel", channelId: c.channel_id, channelName: c.channel_name } });
                            }}
                            onRemove={() =>
                              setRemoving({
                                provider: k.provider,
                                label: k.label,
                                target: { scope: "channel", channelId: c.channel_id, channelName: c.channel_name },
                              })
                            }
                          />
                        ))}
                      </ul>
                    </div>
                  ))}
              </div>
            )}
            {replacing && replacing.target.scope === "channel" && (
              <AddKeyForm
                data={data}
                initial={replacing}
                onCancel={() => setReplacing(null)}
                onSaved={(m) => {
                  setReplacing(null);
                  setFeedback(m);
                  load();
                }}
              />
            )}
          </section>
        </>
      )}

      <ConfirmDialog
        open={Boolean(removing)}
        busy={busy}
        title={`Remove the ${removing?.label} key?`}
        confirmLabel="Remove key"
        confirmClass={btn.danger}
        onCancel={() => setRemoving(null)}
        onConfirm={confirmRemove}
      >
        <p>
          {removing?.target.scope === "channel"
            ? `The channel ${removing.target.channelName} will use the client-wide key again (if there is one).`
            : removing?.provider === "anthropic"
            ? "The assistant will use the JTS key again, and replies will be billed."
            : "The key is deleted from secure storage."}
        </p>
      </ConfirmDialog>
    </div>
  );
}
