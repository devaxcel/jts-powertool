'use client';

import { useEffect, useState, useCallback, useMemo } from "react";
import { useRouter } from "next/navigation";
import {
  Folder,
  FolderPlus,
  Trash2,
  Edit3,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  Database,
  X,
  Loader2,
  ChevronRight,
  Hash,
  AtSign,
  Radio,
} from "lucide-react";
import {
  fetchFolders,
  createFolder,
  updateFolder,
  deleteFolder,
  fetchUnassignedChannels,
  syncSlackChannels,
  assignChannelFolder,
  getAuthoritativeWorkspace,
  AUTHORITATIVE_CHANNEL_NAMES,
} from "@/lib/api";
import { ChannelFolder, ChannelProject } from "@/lib/types";

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
      const [foldersData, unassignedData] = await Promise.all([
        fetchFolders().catch(() => []),
        fetchUnassignedChannels().catch(() => []),
      ]);
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
    setIsEditFolderModalOpen(true);
  }

  async function handleUpdateFolder(e: React.FormEvent) {
    e.preventDefault();
    if (!editFolderId || !editFolderNameInput.trim()) return;
    setEditFolderSaving(true);
    try {
      const res = await updateFolder(editFolderId, editFolderNameInput.trim(), editFolderDescInput.trim() || undefined);
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
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 shrink-0 pb-4 border-b border-gray-200">
        <div>
          <div className="flex items-center gap-2.5">
            <div className="h-9 w-9 rounded-xl bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA]">
              <Folder className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-gray-800 flex items-center gap-2">
                <span>Channel Folders</span>
                <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-50 text-emerald-600 border border-emerald-200 flex items-center gap-1">
                  <Database className="h-3 w-3" />
                  PostgreSQL DB
                </span>
                <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-gray-100 text-gray-600 border border-gray-200 flex items-center gap-1">
                  <Radio className="h-3 w-3" />
                  Slack Bot Synced
                </span>
              </h1>
              <p className="text-xs text-gray-500 mt-0.5">
                Organize Slack channels &amp; members into project folders.
              </p>
            </div>
          </div>
        </div>

        {/* Right Corner: Action Buttons */}
        <div className="flex items-center gap-2.5">
          <button
            onClick={handleSyncSlack}
            disabled={syncing || loading}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-medium border border-gray-200 transition disabled:opacity-50"
            title="Scan Slack for all channels and DMs where the bot is present"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${syncing ? "animate-spin text-[#088ADA]" : ""}`} />
            <span>{syncing ? "Syncing..." : "Sync Slack"}</span>
          </button>

          <button
            onClick={() => loadData()}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-medium border border-gray-200 transition disabled:opacity-50"
            title="Reload data from database"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            <span>Refresh</span>
          </button>

          {isMasterAdmin && (
            <button
              onClick={openCreateFolderModal}
              className="flex items-center gap-2 px-4 py-2 rounded-xl bg-[#088ADA] hover:bg-[#0778bd] text-white text-xs sm:text-sm font-semibold shadow-lg shadow-sky-600/20 transition active:scale-95"
              title="Create a new folder"
            >
              <FolderPlus className="h-4 w-4" />
              <span>Create Folder</span>
            </button>
          )}
        </div>
      </div>

      {/* Feedback Banner */}
      {feedback && (
        <div
          className={`flex items-center justify-between p-3.5 rounded-xl border text-xs sm:text-sm transition-all animate-in fade-in slide-in-from-top-1 ${
            feedback.type === "success"
              ? "bg-emerald-50 border-emerald-200 text-emerald-600"
              : "bg-rose-50 border-rose-200 text-rose-600"
          }`}
        >
          <div className="flex items-center gap-2.5">
            {feedback.type === "success" ? (
              <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
            ) : (
              <AlertCircle className="h-4 w-4 shrink-0 text-rose-500" />
            )}
            <span>{feedback.message}</span>
          </div>
          <button
            onClick={() => setFeedback(null)}
            className="text-gray-400 hover:text-gray-600 p-1 rounded-lg hover:bg-gray-100 transition"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* Main Content Area */}
      <div className="space-y-8 flex-1">
        {loading ? (
          <div className="h-64 flex flex-col items-center justify-center gap-3 text-gray-500">
            <Loader2 className="h-8 w-8 animate-spin text-[#088ADA]" />
            <p className="text-sm">Loading folders from database...</p>
          </div>
        ) : (
          <>
            {/* Section 1: Folders Table */}
            <div>
              <div className="flex items-center justify-between mb-4">
                <span className="text-xs font-semibold text-gray-500 uppercase tracking-wider">
                  Folders ({displayedFolders.length})
                </span>
              </div>

              {displayedFolders.length === 0 ? (
                <div className="h-48 flex flex-col items-center justify-center rounded-2xl border border-dashed border-gray-200 bg-gray-50 p-6 text-center">
                  <div className="h-10 w-10 rounded-xl bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA] mb-2">
                    <Folder className="h-5 w-5" />
                  </div>
                  <h3 className="text-xs font-semibold text-gray-700">No Folders Available</h3>
                  <p className="text-[11px] text-gray-400 max-w-sm mt-0.5 mb-3">
                    No folders assigned to your account.
                  </p>
                  {isMasterAdmin && (
                    <button
                      onClick={openCreateFolderModal}
                      className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-[#088ADA] hover:bg-[#0778bd] text-white text-xs font-semibold transition"
                    >
                      <FolderPlus className="h-3.5 w-3.5" />
                      <span>Create First Folder</span>
                    </button>
                  )}
                </div>
              ) : (
                <div className="overflow-x-auto rounded-xl border border-gray-200 shadow-sm">
                  <table className="w-full text-left border-collapse">
                    <thead className="bg-[#088ADA] text-white text-xs uppercase tracking-wider sticky top-0 z-20 shadow-sm border-b border-gray-300">
                      <tr>
                        <th className="p-3 font-semibold bg-[#088ADA] text-white">Folder Name</th>
                        <th className="p-3 font-semibold bg-[#088ADA] text-white">Description</th>
                        <th className="p-3 font-semibold text-center bg-[#088ADA] text-white">Channels</th>
                        <th className="p-3 font-semibold text-center bg-[#088ADA] text-white">Status</th>
                        <th className="p-3 font-semibold text-right bg-[#088ADA] text-white">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {displayedFolders.map((folder, idx) => (
                        <tr
                          key={folder.id}
                          onClick={() => router.push(`/folders/${folder.id}`)}
                          className={`cursor-pointer transition hover:bg-gray-200 ${idx % 2 === 0 ? "bg-white" : "bg-[#ededed]"}`}
                        >
                          <td className="p-3">
                            <div className="flex items-center gap-2.5">
                              <div className="h-8 w-8 rounded-lg bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA] shrink-0">
                                <Folder className="h-4 w-4" />
                              </div>
                              <span className="text-sm font-semibold text-gray-800">{folder.name}</span>
                            </div>
                          </td>
                          <td className="p-3 text-xs text-gray-600 max-w-xs truncate">
                            {folder.description || <span className="italic text-gray-400">No description</span>}
                          </td>
                          <td className="p-3 text-center">
                            <span className="inline-flex items-center gap-1 text-xs font-medium text-[#0778bd] bg-gray-100 border border-gray-200 px-2 py-0.5 rounded-full">
                              <Hash className="h-3 w-3" />
                              {folder.channel_count}
                            </span>
                          </td>
                          <td className="p-3 text-center">
                            <span className="inline-flex items-center gap-1 text-[10px] font-medium text-emerald-600 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded-full">
                              <Database className="h-3 w-3" />
                              Saved
                            </span>
                          </td>
                          <td className="p-3 text-right">
                            <div className="flex items-center justify-end gap-1">
                              {isMasterAdmin && (
                                <>
                                  <button
                                    onClick={(e) => { e.stopPropagation(); openEditFolderModal(folder); }}
                                    className="p-1.5 text-gray-500 hover:text-[#0778bd] hover:bg-gray-300 rounded-lg transition"
                                    title="Rename / Edit folder"
                                  >
                                    <Edit3 className="h-3.5 w-3.5" />
                                  </button>
                                  <button
                                    onClick={(e) => { e.stopPropagation(); handleDeleteFolder(folder); }}
                                    className="p-1.5 text-gray-500 hover:text-rose-600 hover:bg-rose-100 rounded-lg transition"
                                    title="Delete folder"
                                  >
                                    <Trash2 className="h-3.5 w-3.5" />
                                  </button>
                                </>
                              )}
                              <ChevronRight className="h-4 w-4 text-gray-400 ml-1" />
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Section 2: Unassigned Slack Channels */}
            {isMasterAdmin && (
            <div className="pt-4 border-t border-gray-200">
              <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold text-gray-700 uppercase tracking-wider">
                    Unassigned Slack Channels ({dedupedUnassignedChannels.length})
                  </span>
                  <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-gray-100 text-[#088ADA] border border-gray-200">
                    Bot Discovered
                  </span>
                </div>
                <button
                  onClick={handleSyncSlack}
                  disabled={syncing}
                  className="flex items-center gap-1.5 text-xs text-[#088ADA] hover:text-[#0778bd] font-medium transition"
                >
                  <RefreshCw className={`h-3 w-3 ${syncing ? "animate-spin" : ""}`} />
                  <span>Scan Slack</span>
                </button>
              </div>

              {dedupedUnassignedChannels.length === 0 ? (
                <div className="p-6 rounded-2xl border border-gray-200 bg-gray-50 flex items-center justify-between text-xs text-gray-500">
                  <div className="flex items-center gap-2.5">
                    <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0" />
                    <span>All discovered channels and members are organized into folders.</span>
                  </div>
                  <button
                    onClick={handleSyncSlack}
                    disabled={syncing}
                    className="px-3 py-1.5 rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-medium transition"
                  >
                    Check for New Slack Channels
                  </button>
                </div>
              ) : (
                <div className="overflow-x-auto rounded-xl border border-gray-200 shadow-sm">
                  <table className="w-full text-left border-collapse">
                    <thead className="bg-[#088ADA] text-white text-xs uppercase tracking-wider sticky top-0 z-20 shadow-sm border-b border-gray-300">
                      <tr>
                        <th className="p-3 font-semibold bg-[#088ADA] text-white">Workspace ID</th>
                        <th className="p-3 font-semibold bg-[#088ADA] text-white">Slack Workspace</th>
                        <th className="p-3 font-semibold bg-[#088ADA] text-white">Channel / Member</th>
                        <th className="p-3 font-semibold text-center bg-[#088ADA] text-white">Type</th>
                        <th className="p-3 font-semibold bg-[#088ADA] text-white">Assign to Folder</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {dedupedUnassignedChannels.map((channel, idx) => {
                        const isDm = isDmChannel(channel.channel_name, channel.channel_id);
                        const isAssigning = assigningChannelId === channel.channel_id;
                        return (
                          <tr key={`${channel.workspace_id || ''}-${channel.channel_id}-${idx}`} className={`transition hover:bg-gray-200 ${idx % 2 === 0 ? "bg-white" : "bg-[#ededed]"}`}>
                            <td className="p-3 font-mono text-xs">
                              <span className="px-2 py-0.5 rounded bg-blue-100 text-blue-800 font-mono font-bold text-[10px] border border-blue-200">
                                {channel.workspace_id || "T5ZMF56H5"}
                              </span>
                            </td>
                            <td className="p-3 text-xs font-bold text-gray-900">{channel.workspace_name || "Axcel World"}</td>
                            <td className="p-3">
                              <div className="flex items-center gap-2.5">
                                <div className="h-7 w-7 rounded-lg bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA] shrink-0">
                                  {isDm ? <AtSign className="h-3.5 w-3.5" /> : <Hash className="h-3.5 w-3.5" />}
                                </div>
                                <div>
                                  <div className="text-sm font-semibold text-gray-800">{channel.channel_name}</div>
                                  <div className="font-mono text-[10px] text-gray-400">{channel.channel_id}</div>
                                </div>
                              </div>
                            </td>
                            <td className="p-3 text-center">
                              <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${isDm ? "bg-gray-100 text-[#088ADA]" : "bg-emerald-50 text-emerald-600 border border-emerald-200"}`}>
                                {isDm ? "DM" : "Channel"}
                              </span>
                            </td>
                            <td className="p-3">
                              <select
                                defaultValue=""
                                disabled={isAssigning || folders.length === 0}
                                onChange={(e) => {
                                  if (e.target.value) {
                                    handleAssignChannelToFolder(channel.channel_id, Number(e.target.value));
                                  }
                                }}
                                className="px-2.5 py-1 bg-white border border-gray-300 rounded-lg text-gray-800 text-xs focus:outline-none focus:border-[#088ADA] transition cursor-pointer w-full max-w-[180px]"
                              >
                                <option value="" disabled>Select Folder...</option>
                                {folders.map((f) => (
                                  <option key={f.id} value={f.id}>{f.name}</option>
                                ))}
                              </select>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
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
                  <h3 className="text-sm font-semibold text-gray-800">Create Folder</h3>
                  <p className="text-[11px] text-gray-500">Saved permanently to database</p>
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
                    placeholder="Enter folder name (e.g. Production Projects)"
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
                      <span>Saving to Database...</span>
                    </>
                  ) : (
                    <>
                      <FolderPlus className="h-3.5 w-3.5" />
                      <span>Create Folder</span>
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
                  <h3 className="text-sm font-semibold text-gray-800">Edit Folder</h3>
                  <p className="text-[11px] text-gray-500">Update folder name or description</p>
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
