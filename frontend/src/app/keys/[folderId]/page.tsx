'use client';

import { useEffect, useState, useCallback, useMemo } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  Folder,
  Hash,
  AtSign,
  Plus,
  ArrowLeft,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  Database,
  Trash2,
  X,
  Loader2,
  Radio,
  Search,
  Check,
} from "lucide-react";
import {
  fetchFolder,
  fetchUnassignedChannels,
  syncSlackChannels,
  assignChannelFolder,
} from "@/lib/api";
import { FolderDetails, ChannelProject } from "@/lib/types";

export default function FolderDetailPage() {
  const params = useParams();
  const folderId = params?.folderId as string;

  const [folder, setFolder] = useState<FolderDetails | null>(null);
  const [channels, setChannels] = useState<ChannelProject[]>([]);
  const [unassignedChannels, setUnassignedChannels] = useState<ChannelProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  // Search filter for unassigned channels
  const [unassignedSearchQuery, setUnassignedSearchQuery] = useState("");
  const [assigningChannelId, setAssigningChannelId] = useState<string | null>(null);

  const loadData = useCallback(async () => {
    if (!folderId) return;
    setLoading(true);
    try {
      const [folderRes, unassignedRes] = await Promise.all([
        fetchFolder(folderId),
        fetchUnassignedChannels().catch(() => []),
      ]);
      setFolder(folderRes.folder);
      setChannels(folderRes.channels || []);
      setUnassignedChannels(
        (unassignedRes || []).filter((c: ChannelProject) => !c.folder_id)
      );
    } catch (err: any) {
      console.error("Error loading folder details:", err);
      setFeedback({ type: "error", message: err?.message || "Failed to load folder details" });
    } finally {
      setLoading(false);
    }
  }, [folderId]);

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

  // Quick move channel to this folder
  async function handleAddChannelToFolder(channel: ChannelProject) {
    if (!folder) return;
    setAssigningChannelId(channel.channel_id);
    try {
      const res = await assignChannelFolder(channel.channel_id, folder.id);
      setFeedback({
        type: "success",
        message: res.message || `Channel '${channel.channel_name}' added to folder '${folder.name}'!`,
      });
      setUnassignedChannels((prev) => prev.filter((c) => c.channel_id !== channel.channel_id));
      setChannels((prev) => [...prev, { ...channel, folder_id: folder.id, folder_name: folder.name }]);
    } catch (err: any) {
      setFeedback({ type: "error", message: err?.message || "Failed to add channel to folder" });
    } finally {
      setAssigningChannelId(null);
    }
  }

  // Remove channel from this folder
  async function handleRemoveChannelFromFolder(channel: ChannelProject) {
    if (!confirm(`Are you sure you want to remove '${channel.channel_name}' from this folder?`)) {
      return;
    }
    try {
      const res = await assignChannelFolder(channel.channel_id, null as any);
      setFeedback({
        type: "success",
        message: res.message || `Channel '${channel.channel_name}' removed from folder.`,
      });
      setChannels((prev) => prev.filter((c) => c.channel_id !== channel.channel_id));
      const unassignedCopy = { ...channel, folder_id: null, folder_name: null };
      setUnassignedChannels((prev) => [...prev, unassignedCopy]);
    } catch (err: any) {
      setFeedback({ type: "error", message: err?.message || "Failed to remove channel from folder" });
    }
  }

  const isDmChannel = (name: string, id: string) => {
    return id.startsWith("D") || name.startsWith("@");
  };

  const dedupedFolderChannels = useMemo(() => {
    const seenKeys = new Set<string>();
    const result: ChannelProject[] = [];

    for (const c of channels) {
      if (!c.channel_id) continue;
      const wsKey = c.workspace_id || c.workspace_name || "";
      const key = `${wsKey}::${c.channel_id.trim().toLowerCase()}`;
      if (seenKeys.has(key)) continue;

      seenKeys.add(key);
      result.push(c);
    }
    return result;
  }, [channels]);

  const filteredUnassignedChannels = useMemo(() => {
    const folderKeys = new Set(dedupedFolderChannels.map((c) => `${c.workspace_id || c.workspace_name || ''}::${c.channel_id.trim().toLowerCase()}`));
    const seenKeys = new Set<string>();
    const result: ChannelProject[] = [];

    for (const c of unassignedChannels) {
      if (!c.channel_id) continue;
      const wsKey = c.workspace_id || c.workspace_name || "";
      const key = `${wsKey}::${c.channel_id.trim().toLowerCase()}`;

      if (folderKeys.has(key)) continue;
      if (seenKeys.has(key)) continue;

      if (unassignedSearchQuery.trim()) {
        const q = unassignedSearchQuery.toLowerCase();
        const matchName = c.channel_name.toLowerCase().includes(q);
        const matchId = c.channel_id.toLowerCase().includes(q);
        const matchWs = (c.workspace_name || "").toLowerCase().includes(q) || (c.workspace_id || "").toLowerCase().includes(q);
        if (!matchName && !matchId && !matchWs) continue;
      }

      seenKeys.add(key);
      result.push(c);
    }
    return result;
  }, [unassignedChannels, dedupedFolderChannels, unassignedSearchQuery]);

  return (
    <div className="space-y-6 max-w-7xl mx-auto flex flex-col min-h-[calc(100vh-8rem)]">
      {/* Top Navigation & Breadcrumb */}
      <div className="flex items-center justify-between">
        <Link
          href="/keys"
          className="inline-flex items-center gap-1.5 text-xs font-semibold text-gray-500 hover:text-gray-800 transition bg-gray-100 hover:bg-gray-200 border border-gray-200 px-3 py-1.5 rounded-lg"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          <span>Back to All Folders</span>
        </Link>
      </div>

      {/* Header Section */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-gray-200">
        <div>
          <div className="flex items-center gap-2.5">
            <div className="h-10 w-10 rounded-xl bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA] shrink-0">
              <Folder className="h-5 w-5" />
            </div>
            <div>
              <div className="flex items-center gap-2">
                <h1 className="text-xl font-bold text-gray-800">
                  {folder?.name || (loading ? "Loading Folder..." : `Folder #${folderId}`)}
                </h1>
                <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-50 text-emerald-600 border border-emerald-200 flex items-center gap-1">
                  <Database className="h-3 w-3" />
                  PostgreSQL DB
                </span>
                <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-gray-100 text-gray-600 border border-gray-200 flex items-center gap-1">
                  <Radio className="h-3 w-3" />
                  Slack Bot Synced
                </span>
              </div>
              <p className="text-xs text-gray-500 mt-0.5">
                {folder?.description || "Manage Slack channels and members associated with this folder."}
              </p>
            </div>
          </div>
        </div>

        {/* Top-Right Action Buttons */}
        <div className="flex items-center gap-2.5">
          <button
            onClick={handleSyncSlack}
            disabled={syncing || loading}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-medium border border-gray-200 transition disabled:opacity-50 shadow-sm"
            title="Scan Slack for all channels and DMs where the bot is added"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${syncing ? "animate-spin text-[#088ADA]" : ""}`} />
            <span>{syncing ? "Syncing Slack..." : "Sync Slack"}</span>
          </button>

          <button
            onClick={() => loadData()}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-medium border border-gray-200 transition disabled:opacity-50"
            title="Reload folder data"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            <span>Refresh</span>
          </button>
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

      {/* Content Area */}
      <div className="space-y-8 flex-1">
        {loading ? (
          <div className="h-64 flex flex-col items-center justify-center gap-3 text-gray-500">
            <Loader2 className="h-8 w-8 animate-spin text-[#088ADA]" />
            <p className="text-sm">Loading folder channels and Slack status...</p>
          </div>
        ) : (
          <>
            {/* Section 1: Channels Added to this Folder */}
            <div>
              <div className="flex items-center justify-between mb-4">
                <span className="text-xs font-semibold text-gray-700 uppercase tracking-wider">
                  Channels &amp; Members in this Folder ({dedupedFolderChannels.length})
                </span>
              </div>

              {dedupedFolderChannels.length === 0 ? (
                <div className="p-6 rounded-2xl border border-dashed border-gray-200 bg-gray-100 text-center">
                  <p className="text-xs text-gray-500">
                    No channels or members have been added to this folder yet.
                  </p>
                  <p className="text-[11px] text-gray-400 mt-1">
                    Select any available channel from the list below to associate it with this folder.
                  </p>
                </div>
              ) : (
                <div className="overflow-hidden rounded-xl border border-gray-200 shadow-sm">
                  <table className="w-full text-left">
                    <thead className="bg-[#088ADA] text-white text-xs uppercase tracking-wider sticky top-0 z-20 shadow-sm border-b border-gray-300">
                      <tr>
                        <th className="p-3 font-semibold bg-[#088ADA] text-white">Channel / Member</th>
                        <th className="p-3 font-semibold text-center bg-[#088ADA] text-white">Type</th>
                        <th className="p-3 font-semibold text-right bg-[#088ADA] text-white">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {dedupedFolderChannels.map((channel, idx) => {
                        const isDm = isDmChannel(channel.channel_name, channel.channel_id);
                        return (
                          <tr key={channel.channel_id} className={`hover:bg-gray-100 transition ${idx % 2 === 0 ? "bg-white" : "bg-gray-50"}`}>
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
                            <td className="p-3 text-right">
                              <button
                                onClick={() => handleRemoveChannelFromFolder(channel)}
                                className="p-1.5 text-gray-400 hover:text-rose-500 hover:bg-rose-50 rounded-lg transition"
                                title="Remove from this folder"
                              >
                                <Trash2 className="h-3.5 w-3.5" />
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Section 2: All Channels from Slack That Are NOT Part of Any Folder */}
            <div className="pt-6 border-t border-gray-200">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-gray-700 uppercase tracking-wider">
                      Available Slack Channels ({filteredUnassignedChannels.length})
                    </span>
                    <span className="px-2 py-0.5 rounded-full text-[10px] font-medium bg-gray-100 text-[#088ADA] border border-gray-200">
                      Not in Any Folder
                    </span>
                  </div>
                  <p className="text-[11px] text-gray-400 mt-0.5">
                    Channels and members discovered from Slack. Adding one to this folder removes it from this list.
                  </p>
                </div>

                {/* Search & Sync toolbar */}
                <div className="flex items-center gap-2">
                  <div className="relative">
                    <Search className="absolute left-2.5 top-2 h-3.5 w-3.5 text-gray-400" />
                    <input
                      type="text"
                      value={unassignedSearchQuery}
                      onChange={(e) => setUnassignedSearchQuery(e.target.value)}
                      placeholder="Filter channels..."
                      className="pl-8 pr-3 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-gray-800 text-xs placeholder-gray-400 focus:outline-none focus:border-[#088ADA] transition w-44 sm:w-56"
                    />
                  </div>
                  <button
                    onClick={handleSyncSlack}
                    disabled={syncing}
                    className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-medium border border-gray-200 transition shrink-0"
                    title="Scan Slack for new channels"
                  >
                    <RefreshCw className={`h-3.5 w-3.5 ${syncing ? "animate-spin text-[#088ADA]" : ""}`} />
                    <span>Sync</span>
                  </button>
                </div>
              </div>

              {filteredUnassignedChannels.length === 0 ? (
                <div className="p-6 rounded-2xl border border-gray-200 bg-gray-100 flex flex-col sm:flex-row items-center justify-between gap-4 text-xs text-gray-500">
                  <div className="flex items-center gap-2.5">
                    <Check className="h-4 w-4 text-emerald-600 shrink-0" />
                    <span>
                      {unassignedSearchQuery
                        ? "No unassigned channels match your filter."
                        : "All discovered Slack channels and members are currently added to folders."}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="text-[11px] text-gray-400">Need another channel?</span>
                    <button
                      onClick={handleSyncSlack}
                      disabled={syncing}
                      className="px-3 py-1.5 rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-700 border border-gray-200 text-xs font-medium transition"
                    >
                      Invite Bot in Slack &amp; Sync
                    </button>
                  </div>
                </div>
              ) : (
                <div className="overflow-hidden rounded-xl border border-gray-200 shadow-sm">
                  <table className="w-full text-left">
                    <thead className="bg-[#088ADA] text-white text-xs uppercase tracking-wider sticky top-0 z-20 shadow-sm border-b border-gray-300">
                      <tr>
                        <th className="p-3 font-semibold bg-[#088ADA] text-white">Channel / Member</th>
                        <th className="p-3 font-semibold text-center bg-[#088ADA] text-white">Type</th>
                        <th className="p-3 font-semibold text-right bg-[#088ADA] text-white">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100">
                      {filteredUnassignedChannels.map((channel, idx) => {
                        const isDm = isDmChannel(channel.channel_name, channel.channel_id);
                        const isAssigning = assigningChannelId === channel.channel_id;
                        return (
                          <tr key={channel.channel_id} className={`hover:bg-gray-100 transition ${idx % 2 === 0 ? "bg-white" : "bg-gray-50"}`}>
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
                            <td className="p-3 text-right">
                              <button
                                onClick={() => handleAddChannelToFolder(channel)}
                                disabled={isAssigning}
                                className="flex items-center gap-1 px-2.5 py-1 rounded-lg bg-[#088ADA] hover:bg-[#0778bd] text-white text-xs font-medium transition disabled:opacity-50 ml-auto"
                              >
                                {isAssigning ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
                                <span>Add</span>
                              </button>
                            </td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
