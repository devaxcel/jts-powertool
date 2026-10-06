'use client';

import { WorkspaceClientsCard } from "@/components/WorkspaceClientsCard";
import { useEffect, useState, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import {
  Folder,
  FolderPlus,
  Trash2,
  Edit3,
  RefreshCw,
  CheckCircle2,
  X,
  Loader2,
  ChevronRight,
  Hash,
  AtSign,
  Inbox,
  MessagesSquare,
} from "lucide-react";
import {
  fetchFolders,
  createFolder,
  updateFolder,
  setFolderOrganization,
  fetchOrganizations,
  deleteFolder,
  fetchUnassignedChannels,
  syncSlackChannels,
  assignChannelFolder,
  getAuthoritativeWorkspace,
  AUTHORITATIVE_CHANNEL_NAMES,
} from "@/lib/api";
import { DataTable } from "@/components/DataTable";
import { ChannelFolder, ChannelProject } from "@/lib/types";
import { PageHeader, Alert, EmptyState, LoadingState, SectionTitle, Tabs, btn } from "@/components/ui";

function canonicalChannelId(cid: string, ws?: string, channelName?: string): string {
  if (!cid) return "";
  const clean = cid.trim().toUpperCase();
  const wsLower = (ws || "").toLowerCase();
  const nameLower = (channelName || "").toLowerCase();

  // JTS Team workspace channel #jts_powertool MUST be C0C28B8V2PK
  if (wsLower.includes("jts") || ws === "T02HKMBE09K") {
    if (clean === "C08MV3EM9PY" || clean === "C0BMV3EM9PY" || clean === "C0C28B8V2PK" || nameLower === "#jts_powertool" || nameLower === "jts_powertool") {
      return "C0C28B8V2PK";
    }
  }

  // Axcel World workspace channel #jts_powertool MUST be C08MV3EM9PY
  if (wsLower.includes("axcel") || ws === "T5ZMF56H5") {
    if (clean === "C0BMV3EM9PY" || clean === "C08MV3EM9PY" || nameLower === "#jts_powertool" || nameLower === "jts_powertool") {
      return "C08MV3EM9PY";
    }
  }

  if (clean === "C0BMV3EM9PY") return "C08MV3EM9PY";
  if (clean === "C0BV6S5UJ0P") return "C08V6S5UJ0P";
  if (clean === "D0BSLP9LXUZ") return "D08SLP9LXUZ";
  return cid.trim();
}

export default function FoldersPage() {
  const router = useRouter();

  // Core State
  const [folders, setFolders] = useState<ChannelFolder[]>([]);
  const [unassignedChannels, setUnassignedChannels] = useState<ChannelProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  // RBAC State
  const [userRole, setUserRole] = useState<string>("jts_admin");
  const [clientFolderId, setClientFolderId] = useState<number | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined") {
      try {
        const rawUser = sessionStorage.getItem("jts_user");
        const u = JSON.parse(rawUser || "{}");
        const savedSim = sessionStorage.getItem("jts_simulated_role");
        const r = savedSim || u.role || "jts_admin";
        setUserRole(r);
        if (u.client_folder_id) {
          setClientFolderId(Number(u.client_folder_id));
        }
      } catch {}
    }
  }, []);

  const isMasterAdmin = !userRole || userRole === "admin" || userRole === "jts_admin";
  const displayedFolders = (!isMasterAdmin && clientFolderId) ? folders.filter((f) => f.id === clientFolderId) : folders;

  // Create Folder Modal State
  const [isFolderModalOpen, setIsFolderModalOpen] = useState(false);
  const [folderNameInput, setFolderNameInput] = useState("");
  const [folderDescInput, setFolderDescInput] = useState("");
  const [folderSaving, setFolderSaving] = useState(false);

  // Rename/Edit Folder Modal State
  const [isEditFolderModalOpen, setIsEditFolderModalOpen] = useState(false);
  const [editFolderId, setEditFolderId] = useState<number | null>(null);
  const [editFolderNameInput, setEditFolderNameInput] = useState("");
  const [editFolderDescInput, setEditFolderDescInput] = useState("");
  const [editFolderSaving, setEditFolderSaving] = useState(false);
  const [tab, setTab] = useState<"clients" | "workspaces" | "unassigned">("clients");
  const [editFolderOrgInput, setEditFolderOrgInput] = useState<string>("");
  const [editFolderOrgOriginal, setEditFolderOrgOriginal] = useState<string>("");
  const [organizations, setOrganizations] = useState<Array<{ id: number; name: string }>>([]);

  // Assign Channel to Folder state
  const [assigningChannelId, setAssigningChannelId] = useState<string | null>(null);

  const dedupedUnassignedChannels = useMemo(() => {
    const seenKeys = new Set<string>();
    const result: ChannelProject[] = [];

    for (const c of unassignedChannels) {
      if (c.folder_id !== null && c.folder_id !== undefined) continue;
      if (!c.channel_id) continue;

      const cid = canonicalChannelId(c.channel_id, c.workspace_id || c.workspace_name, c.channel_name);
      const authWs = getAuthoritativeWorkspace(cid, c.workspace_id, c.workspace_name);
      const key = cid.toUpperCase();
      if (seenKeys.has(key)) continue;

      seenKeys.add(key);
      let cname = c.channel_name;
      if (AUTHORITATIVE_CHANNEL_NAMES[key]) {
        cname = AUTHORITATIVE_CHANNEL_NAMES[key];
      }
      result.push({
        ...c,
        channel_id: cid,
        channel_name: cname,
        workspace_id: authWs.id,
        workspace_name: authWs.name,
      });
    }
    return result;
  }, [unassignedChannels]);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [foldersData, unassignedData, orgsData] = await Promise.all([
        fetchFolders().catch(() => []),
        fetchUnassignedChannels().catch(() => []),
        fetchOrganizations().catch(() => ({ organizations: [] as any[], total: 0 })),
      ]);
      setOrganizations((orgsData.organizations || []).map((o: any) => ({ id: o.id, name: o.name })));
      const normalizedUnassigned = (unassignedData || []).map((ch: ChannelProject) => {
        const cid = canonicalChannelId(ch.channel_id, ch.workspace_id || ch.workspace_name, ch.channel_name);
        const authWs = getAuthoritativeWorkspace(cid, ch.workspace_id, ch.workspace_name);
        let cname = ch.channel_name;
        if (AUTHORITATIVE_CHANNEL_NAMES[cid.toUpperCase()]) {
          cname = AUTHORITATIVE_CHANNEL_NAMES[cid.toUpperCase()];
        }
        return {
          ...ch,
          channel_id: cid,
          channel_name: cname,
          workspace_id: authWs.id,
          workspace_name: authWs.name,
        };
      });
      setFolders(foldersData);
      setUnassignedChannels(normalizedUnassigned);
    } catch (e: any) {
      console.error("Error loading folders data:", e);
      setFeedback({ type: "error", message: e?.message || "Failed to load folders from database" });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  // Sync Slack channels
  async function handleSyncSlack() {
    setSyncing(true);
    try {
      const res = await syncSlackChannels();
      setFeedback({
        type: "success",
        message: res.message || `Discovered ${res.synced_count} channels and conversations from Slack!`,
      });
      await loadData();
    } catch (err: any) {
      setFeedback({ type: "error", message: err?.message || "Failed to sync Slack channels" });
    } finally {
      setSyncing(false);
    }
  }

  // --- Folder Actions ---
  function openCreateFolderModal() {
    setFolderNameInput("");
    setFolderDescInput("");
    setIsFolderModalOpen(true);
  }

  async function handleCreateFolder(e: React.FormEvent) {
    e.preventDefault();
    if (!folderNameInput.trim()) {
      setFeedback({ type: "error", message: "Folder name is required." });
      return;
    }
    setFolderSaving(true);
    try {
      const res = await createFolder(folderNameInput.trim(), folderDescInput.trim() || undefined);
      setFeedback({
        type: "success",
        message: res.message || `Folder '${folderNameInput.trim()}' created and saved to database.`,
      });
      setIsFolderModalOpen(false);
      setFolderNameInput("");
      setFolderDescInput("");
      await loadData();
    } catch (err: any) {
      setFeedback({ type: "error", message: err?.message || "Failed to create folder" });
    } finally {
      setFolderSaving(false);
    }
  }

  function openEditFolderModal(folder: ChannelFolder) {
    setEditFolderId(folder.id);
    setEditFolderNameInput(folder.name);
    setEditFolderDescInput(folder.description || "");
    setEditFolderOrgInput(folder.organization_id ? String(folder.organization_id) : "");
    setEditFolderOrgOriginal(folder.organization_id ? String(folder.organization_id) : "");
    setIsEditFolderModalOpen(true);
  }

  async function handleUpdateFolder(e: React.FormEvent) {
    e.preventDefault();
    if (!editFolderId || !editFolderNameInput.trim()) return;
    setEditFolderSaving(true);
    try {
      const res = await updateFolder(editFolderId, editFolderNameInput.trim(), editFolderDescInput.trim() || undefined);
      if (editFolderOrgInput !== editFolderOrgOriginal) {
        await setFolderOrganization(editFolderId, editFolderOrgInput ? Number(editFolderOrgInput) : null);
      }
      setFeedback({ type: "success", message: res.message || "Folder updated in database." });
      setIsEditFolderModalOpen(false);
      await loadData();
    } catch (err: any) {
      setFeedback({ type: "error", message: err?.message || "Failed to update folder" });
    } finally {
      setEditFolderSaving(false);
    }
  }

  async function handleDeleteFolder(folder: ChannelFolder) {
    if (!confirm(`Are you sure you want to permanently delete folder '${folder.name}'?`)) {
      return;
    }
    try {
      const res = await deleteFolder(folder.id);
      setFeedback({ type: "success", message: res.message || "Folder deleted from database." });
      await loadData();
    } catch (err: any) {
      setFeedback({ type: "error", message: err?.message || "Failed to delete folder" });
    }
  }

  // Quick move unassigned channel to a folder
  async function handleAssignChannelToFolder(channelId: string, folderId: number) {
    setAssigningChannelId(channelId);
    try {
      const res = await assignChannelFolder(channelId, folderId);
      setFeedback({
        type: "success",
        message: res.message || `Channel moved to folder!`,
      });
      await loadData();
    } catch (err: any) {
      setFeedback({ type: "error", message: err?.message || "Failed to assign channel to folder" });
    } finally {
      setAssigningChannelId(null);
    }
  }

  const isDmChannel = (name: string, id: string) => {
    return id.startsWith("D") || name.startsWith("@");
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto flex flex-col min-h-[calc(100vh-8rem)]">
      <PageHeader
        icon={Folder}
        title={isMasterAdmin ? "Clients & channels" : "Your channels"}
        description={
          isMasterAdmin
            ? "Each client has a folder. Put a client's Slack channels in their folder so their usage, billing and access stay separate."
            : "The Slack channels that belong to your organization."
        }
        actions={
          <>
            <button
              onClick={handleSyncSlack}
              disabled={syncing || loading}
              className={btn.secondary}
              title="Find every Slack channel and DM the bot has been added to"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${syncing ? "animate-spin" : ""}`} />
              <span>{syncing ? "Checking Slack..." : "Find Slack channels"}</span>
            </button>
            {isMasterAdmin && (
              <button onClick={openCreateFolderModal} className={btn.primary}>
                <FolderPlus className="h-4 w-4" />
                <span>New client folder</span>
              </button>
            )}
          </>
        }
      />

      {feedback && (
        <Alert type={feedback.type} onClose={() => setFeedback(null)}>
          {feedback.message}
        </Alert>
      )}

      {/* Main Content Area */}
      <div className="space-y-8 flex-1">
        {loading ? (
          <LoadingState label="Loading client folders..." />
        ) : (
          <>
            <Tabs
              value={tab}
              onChange={setTab}
              tabs={[
                { id: "clients" as const, label: "Clients", count: displayedFolders.length, icon: Folder },
                ...(isMasterAdmin
                  ? [
                      { id: "workspaces" as const, label: "Slack workspaces", icon: MessagesSquare },
                      { id: "unassigned" as const, label: "Unassigned chats", count: dedupedUnassignedChannels.length, icon: Inbox },
                    ]
                  : []),
              ]}
            />

            {/* Tab 1: client folders */}
            {tab === "clients" && (
            <div>
              <SectionTitle
                title={`Client folders (${displayedFolders.length})`}
                description="Click a folder to see its channels, messages and API key settings."
              />

              {displayedFolders.length === 0 ? (
                <EmptyState
                  icon={Folder}
                  title={isMasterAdmin ? "No client folders yet" : "No folder is assigned to your account"}
                  description={
                    isMasterAdmin
                      ? "Create a folder for each client, then add their Slack channels to it."
                      : "Ask your JTS administrator to assign your account to your organization's folder."
                  }
                  action={
                    isMasterAdmin && (
                      <button onClick={openCreateFolderModal} className={btn.primary}>
                        <FolderPlus className="h-3.5 w-3.5" />
                        <span>Create the first folder</span>
                      </button>
                    )
                  }
                />
              ) : (
                <div className="rounded-xl border border-gray-200 shadow-sm overflow-hidden bg-white">
                  <DataTable
                    rows={displayedFolders}
                    rowKey={(folder) => folder.id}
                    itemLabel="clients"
                    searchPlaceholder="Search client"
                    initialSort={{ key: "name", dir: "asc" }}
                    onRowClick={(folder) => router.push(`/folders/${folder.id}`)}
                    columns={[
                      {
                        key: "name",
                        header: "Client folder",
                        sortValue: (folder) => (folder.name || "").toLowerCase(),
                        render: (folder) => (
                          <div className="flex items-center gap-2.5">
                            <div className="h-8 w-8 rounded-lg bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA] shrink-0">
                              <Folder className="h-4 w-4" />
                            </div>
                            <span className="text-sm font-semibold text-gray-800">{folder.name}</span>
                          </div>
                        ),
                      },
                      {
                        key: "description",
                        header: "Description",
                        className: "text-xs text-gray-600 max-w-xs truncate",
                        render: (folder) => folder.description || <span className="italic text-gray-400">No description</span>,
                      },
                      {
                        key: "channel_count",
                        header: "Channels",
                        align: "center",
                        searchValue: () => "",
                        render: (folder) => (
                          <span className="inline-flex items-center gap-1 text-xs font-medium text-[#0778bd] bg-gray-100 border border-gray-200 px-2 py-0.5 rounded-full">
                            <Hash className="h-3 w-3" />
                            {folder.channel_count}
                          </span>
                        ),
                      },
                      {
                        key: "actions",
                        header: "Actions",
                        sortable: false,
                        searchValue: () => "",
                        align: "right",
                        render: (folder) => (
                          <div className="flex items-center justify-end gap-1">
                            {isMasterAdmin && (
                              <>
                                <button
                                  onClick={(e) => { e.stopPropagation(); openEditFolderModal(folder); }}
                                  className="p-1.5 text-gray-500 hover:text-[#0778bd] hover:bg-gray-200 rounded-lg transition"
                                  title="Rename or edit this folder"
                                  aria-label="Edit folder"
                                >
                                  <Edit3 className="h-3.5 w-3.5" />
                                </button>
                                <button
                                  onClick={(e) => { e.stopPropagation(); handleDeleteFolder(folder); }}
                                  className="p-1.5 text-gray-500 hover:text-rose-600 hover:bg-rose-100 rounded-lg transition"
                                  title="Delete this folder (its channels are kept, just unassigned)"
                                  aria-label="Delete folder"
                                >
                                  <Trash2 className="h-3.5 w-3.5" />
                                </button>
                              </>
                            )}
                            <ChevronRight className="h-4 w-4 text-gray-400 ml-1" />
                          </div>
                        ),
                      },
                    ]}
                  />
                </div>
              )}
            </div>
            )}

            {/* Tab 2: Slack workspaces */}
            {isMasterAdmin && tab === "workspaces" && <WorkspaceClientsCard folders={displayedFolders} />}

            {/* Tab 3: chats not in a folder yet */}
            {isMasterAdmin && tab === "unassigned" && (
            <div>
              <SectionTitle
                title={`Channels not in a folder yet (${dedupedUnassignedChannels.length})`}
                description="Slack channels and DMs the bot is in that don't belong to a client yet. Pick a folder to assign each one."
              />

              {dedupedUnassignedChannels.length === 0 ? (
                <div className="p-4 rounded-2xl border border-gray-200 bg-white flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-sm text-gray-600">
                  <div className="flex items-center gap-2.5">
                    <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" />
                    <span>Every channel the bot is in has been assigned to a client.</span>
                  </div>
                  <button onClick={handleSyncSlack} disabled={syncing} className={btn.secondary}>
                    <RefreshCw className={`h-3.5 w-3.5 ${syncing ? "animate-spin" : ""}`} />
                    Check Slack for new channels
                  </button>
                </div>
              ) : (
                <div className="rounded-xl border border-gray-200 shadow-sm overflow-hidden bg-white">
                  <DataTable
                    rows={dedupedUnassignedChannels}
                    rowKey={(channel, idx) => `${channel.workspace_id || ""}-${channel.channel_id}-${idx}`}
                    itemLabel="channels"
                    initialPageSize={10}
                    searchPlaceholder="Search channel or workspace"
                    initialSort={{ key: "channel_name", dir: "asc" }}
                    columns={[
                      {
                        key: "workspace_id",
                        header: "Workspace ID",
                        render: (channel) => (
                          <span className="px-2 py-0.5 rounded bg-blue-50 text-blue-800 font-mono font-bold text-[10px] border border-blue-200">
                            {channel.workspace_id || "—"}
                          </span>
                        ),
                      },
                      {
                        key: "workspace_name",
                        header: "Slack workspace",
                        className: "text-xs font-semibold text-gray-900",
                        render: (channel) => channel.workspace_name || "—",
                      },
                      {
                        key: "channel_name",
                        header: "Channel or DM",
                        sortValue: (channel) => (channel.channel_name || "").toLowerCase(),
                        searchValue: (channel) => `${channel.channel_name || ""} ${channel.channel_id}`,
                        render: (channel) => {
                          const isDm = isDmChannel(channel.channel_name, channel.channel_id);
                          return (
                            <div className="flex items-center gap-2.5">
                              <div className="h-7 w-7 rounded-lg bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA] shrink-0">
                                {isDm ? <AtSign className="h-3.5 w-3.5" /> : <Hash className="h-3.5 w-3.5" />}
                              </div>
                              <div>
                                <div className="text-sm font-semibold text-gray-800">{channel.channel_name}</div>
                                <div className="font-mono text-[10px] text-gray-400">{channel.channel_id}</div>
                              </div>
                            </div>
                          );
                        },
                      },
                      {
                        key: "type",
                        header: "Type",
                        align: "center",
                        sortValue: (channel) => (isDmChannel(channel.channel_name, channel.channel_id) ? "DM" : "Channel"),
                        render: (channel) => {
                          const isDm = isDmChannel(channel.channel_name, channel.channel_id);
                          return (
                            <span
                              className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${
                                isDm ? "bg-gray-100 text-[#088ADA]" : "bg-emerald-50 text-emerald-600 border border-emerald-200"
                              }`}
                            >
                              {isDm ? "DM" : "Channel"}
                            </span>
                          );
                        },
                      },
                      {
                        key: "assign",
                        header: "Assign to client",
                        sortable: false,
                        searchValue: () => "",
                        render: (channel) => (
                          <select
                            defaultValue=""
                            disabled={assigningChannelId === channel.channel_id || folders.length === 0}
                            onChange={(e) => {
                              if (e.target.value) {
                                handleAssignChannelToFolder(channel.channel_id, Number(e.target.value));
                              }
                            }}
                            className="px-2.5 py-1 bg-white border border-gray-300 rounded-lg text-gray-800 text-xs focus:outline-none focus:border-[#088ADA] transition cursor-pointer w-full max-w-[180px]"
                          >
                            <option value="" disabled>
                              {folders.length === 0 ? "Create a folder first" : "Choose a client..."}
                            </option>
                            {folders.map((f) => (
                              <option key={f.id} value={f.id}>
                                {f.name}
                              </option>
                            ))}
                          </select>
                        ),
                      },
                    ]}
                  />
                </div>
              )}
            </div>
            )}
          </>
        )}
      </div>

      {/* Pop-up Modal: Create Folder */}
      {isFolderModalOpen && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="bg-white border border-gray-200 shadow-2xl rounded-2xl w-full max-w-md overflow-hidden animate-in zoom-in-95 duration-150">
            <div className="p-5 border-b border-gray-200 flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <div className="h-8 w-8 rounded-lg bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA]">
                  <FolderPlus className="h-4 w-4" />
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-gray-800">New client folder</h3>
                  <p className="text-xs text-gray-500">Usually one folder per client company</p>
                </div>
              </div>
              <button
                onClick={() => setIsFolderModalOpen(false)}
                className="text-gray-400 hover:text-gray-600 p-1.5 rounded-lg hover:bg-gray-100 transition"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <form onSubmit={handleCreateFolder}>
              <div className="p-5 space-y-4">
                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1.5">
                    Folder Name <span className="text-rose-500">*</span>
                  </label>
                  <input
                    type="text"
                    required
                    autoFocus
                    value={folderNameInput}
                    onChange={(e) => setFolderNameInput(e.target.value)}
                    placeholder="e.g. Acme Corporation"
                    className="w-full px-3.5 py-2.5 bg-gray-50 border border-gray-200 rounded-xl text-gray-800 text-sm placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:ring-1 focus:ring-[#088ADA] transition"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1.5">
                    Description <span className="text-gray-400 text-[11px]">(Optional)</span>
                  </label>
                  <textarea
                    rows={3}
                    value={folderDescInput}
                    onChange={(e) => setFolderDescInput(e.target.value)}
                    placeholder="Brief description or purpose of this folder..."
                    className="w-full px-3.5 py-2.5 bg-gray-50 border border-gray-200 rounded-xl text-gray-800 text-sm placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:ring-1 focus:ring-[#088ADA] transition resize-none"
                  />
                </div>
              </div>

              <div className="p-4 bg-gray-50 border-t border-gray-200 flex items-center justify-end gap-2.5">
                <button
                  type="button"
                  onClick={() => setIsFolderModalOpen(false)}
                  className="px-4 py-2 rounded-xl bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-semibold transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={folderSaving || !folderNameInput.trim()}
                  className="flex items-center gap-1.5 px-4 py-2 bg-[#088ADA] hover:bg-[#0778bd] text-white text-xs font-semibold rounded-xl shadow-lg shadow-sky-600/20 transition disabled:opacity-50"
                >
                  {folderSaving ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      <span>Creating...</span>
                    </>
                  ) : (
                    <>
                      <FolderPlus className="h-3.5 w-3.5" />
                      <span>Create folder</span>
                    </>
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Pop-up Modal: Edit / Rename Folder */}
      {isEditFolderModalOpen && (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-4 animate-in fade-in duration-200">
          <div className="bg-white border border-gray-200 shadow-2xl rounded-2xl w-full max-w-md overflow-hidden animate-in zoom-in-95 duration-150">
            <div className="p-5 border-b border-gray-200 flex items-center justify-between">
              <div className="flex items-center gap-2.5">
                <div className="h-8 w-8 rounded-lg bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA]">
                  <Edit3 className="h-4 w-4" />
                </div>
                <div>
                  <h3 className="text-sm font-semibold text-gray-800">Edit client folder</h3>
                  <p className="text-xs text-gray-500">Change the name or description</p>
                </div>
              </div>
              <button
                onClick={() => setIsEditFolderModalOpen(false)}
                className="text-gray-400 hover:text-gray-600 p-1.5 rounded-lg hover:bg-gray-100 transition"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <form onSubmit={handleUpdateFolder}>
              <div className="p-5 space-y-4">
                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1.5">
                    Folder Name <span className="text-rose-500">*</span>
                  </label>
                  <input
                    type="text"
                    required
                    autoFocus
                    value={editFolderNameInput}
                    onChange={(e) => setEditFolderNameInput(e.target.value)}
                    placeholder="Folder name"
                    className="w-full px-3.5 py-2.5 bg-gray-50 border border-gray-200 rounded-xl text-gray-800 text-sm placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:ring-1 focus:ring-[#088ADA] transition"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1.5">
                    Description <span className="text-gray-400 text-[11px]">(Optional)</span>
                  </label>
                  <textarea
                    rows={3}
                    value={editFolderDescInput}
                    onChange={(e) => setEditFolderDescInput(e.target.value)}
                    placeholder="Folder description..."
                    className="w-full px-3.5 py-2.5 bg-gray-50 border border-gray-200 rounded-xl text-gray-800 text-sm placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:ring-1 focus:ring-[#088ADA] transition resize-none"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-gray-700 mb-1.5">
                    Billed as (organization) <span className="text-gray-400 text-[11px]">(Optional)</span>
                  </label>
                  <select
                    value={editFolderOrgInput}
                    onChange={(e) => setEditFolderOrgInput(e.target.value)}
                    className="w-full px-3.5 py-2.5 bg-gray-50 border border-gray-200 rounded-xl text-gray-800 text-sm focus:outline-none focus:border-[#088ADA] focus:ring-1 focus:ring-[#088ADA] transition"
                  >
                    <option value="">No organization</option>
                    {organizations.map((o) => (
                      <option key={o.id} value={o.id}>
                        {o.name}
                      </option>
                    ))}
                  </select>
                  <p className="text-[11px] text-gray-500 mt-1">Invoices for this client are made out to this organization automatically.</p>
                </div>
              </div>

              <div className="p-4 bg-gray-50 border-t border-gray-200 flex items-center justify-end gap-2.5">
                <button
                  type="button"
                  onClick={() => setIsEditFolderModalOpen(false)}
                  className="px-4 py-2 rounded-xl bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-semibold transition"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={editFolderSaving || !editFolderNameInput.trim()}
                  className="flex items-center gap-1.5 px-4 py-2 bg-[#088ADA] hover:bg-[#0778bd] text-white text-xs font-semibold rounded-xl shadow-lg shadow-sky-600/20 transition disabled:opacity-50"
                >
                  {editFolderSaving ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      <span>Saving...</span>
                    </>
                  ) : (
                    <span>Save Changes</span>
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
