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
  Trash2,
  X,
  Loader2,
  Search,
  Check,
  MessageSquare,
  GitBranch,
  Wrench,
  ChevronDown,
  CheckSquare,
  KeyRound,
  ShieldCheck,
} from "lucide-react";
import {
  fetchFolder,
  fetchUnassignedChannels,
  syncSlackChannels,
  assignChannelFolder,
  canonicalChannelId,
  getAuthoritativeWorkspace,
  AUTHORITATIVE_CHANNEL_NAMES,
} from "@/lib/api";
import { FolderDetails, ChannelProject } from "@/lib/types";
import { ClientApiKeyCard } from "@/components/ClientApiKeyCard";
import { GithubConnectionCard } from "@/components/GithubConnectionCard";
import { WebsitesCard } from "@/components/WebsitesCard";
import { PageHeader, Alert, EmptyState, LoadingState, SectionTitle, ComingSoonBadge, btn } from "@/components/ui";

interface ToolDefinition {
  id: string;
  name: string;
  icon: any;
  desc: string;
  badge: string;
  badgeColor: string;
  fields: Array<{
    key: string;
    label: string;
    placeholder: string;
    type: string;
    required: boolean;
  }>;
}

const AVAILABLE_TOOLS: ToolDefinition[] = [
  {
    id: "github",
    name: "GitHub",
    icon: GitBranch,
    desc: "Repository management & code commits",
    badge: "Active",
    badgeColor: "bg-blue-50 text-[#088ADA] border-blue-200",
    fields: [
      { key: "token", label: "GitHub Personal Access Token (PAT)", placeholder: "ghp_xxxxxxxxxxxxxxxxxxxx", type: "password", required: true },
      { key: "repo", label: "Default Repository (owner/repo)", placeholder: "acme-corp/main-web-app", type: "text", required: true },
      { key: "branch", label: "Default Branch", placeholder: "main", type: "text", required: false },
      { key: "webhook_secret", label: "Webhook Secret (Optional)", placeholder: "whsec_...", type: "password", required: false },
    ],
  },
  {
    id: "jira",
    name: "Jira Software",
    icon: CheckSquare,
    desc: "Atlassian issue & sprint tracking",
    badge: "Integration",
    badgeColor: "bg-purple-50 text-purple-600 border-purple-200",
    fields: [
      { key: "domain", label: "Jira Domain URL", placeholder: "https://your-company.atlassian.net", type: "text", required: true },
      { key: "email", label: "Atlassian Account Email", placeholder: "dev@company.com", type: "email", required: true },
      { key: "api_token", label: "Jira API Token", placeholder: "ATATT3xFfGF0...", type: "password", required: true },
      { key: "project_key", label: "Default Project Key", placeholder: "PROJ", type: "text", required: false },
    ],
  },
];

export default function FolderDetailPage() {
  const params = useParams();
  const folderId = params?.folderId as string;

  const [folder, setFolder] = useState<FolderDetails | null>(null);
  const [channels, setChannels] = useState<ChannelProject[]>([]);
  const [unassignedChannels, setUnassignedChannels] = useState<ChannelProject[]>([]);
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  // RBAC state
  const [userRole, setUserRole] = useState<string>("jts_admin");
  const isMasterAdmin = !userRole || userRole === "admin" || userRole === "jts_admin";

  // Search filter for unassigned channels
  const [unassignedSearchQuery, setUnassignedSearchQuery] = useState("");
  const [assigningChannelId, setAssigningChannelId] = useState<string | null>(null);

  // Tools dropdown & popup state
  const [activeToolsDropdown, setActiveToolsDropdown] = useState<string | null>(null);
  const [activeToolModal, setActiveToolModal] = useState<{
    channel: ChannelProject;
    tool: ToolDefinition;
  } | null>(null);
  const [toolKeyFields, setToolKeyFields] = useState<Record<string, string>>({});
  const [toolModalFeedback, setToolModalFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  // Close dropdown on click outside
  useEffect(() => {
    const handleGlobalClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement;
      if (!target.closest(".tools-dropdown-container")) {
        setActiveToolsDropdown(null);
      }
    };
    document.addEventListener("mousedown", handleGlobalClick);
    return () => document.removeEventListener("mousedown", handleGlobalClick);
  }, []);

  const loadData = useCallback(async () => {
    if (!folderId) return;
    setLoading(true);
    try {
      const [folderRes, unassignedRes] = await Promise.all([
        fetchFolder(folderId),
        fetchUnassignedChannels().catch(() => []),
      ]);
      setFolder(folderRes.folder);

      const seenFolderKeys = new Set<string>();
      const dedupedFolderChannels: ChannelProject[] = [];
      (folderRes.channels || []).forEach((c: ChannelProject) => {
        const cid = canonicalChannelId(c.channel_id, c.workspace_id || c.workspace_name, c.channel_name);
        const authWs = getAuthoritativeWorkspace(cid, c.workspace_id, c.workspace_name);
        const key = cid.toUpperCase();
        if (!key || seenFolderKeys.has(key)) return;
        seenFolderKeys.add(key);

        let cname = c.channel_name;
        if (AUTHORITATIVE_CHANNEL_NAMES[key]) {
          cname = AUTHORITATIVE_CHANNEL_NAMES[key];
        }
        dedupedFolderChannels.push({
          ...c,
          channel_id: cid,
          channel_name: cname,
          workspace_id: authWs.id,
          workspace_name: authWs.name,
        });
      });
      setChannels(dedupedFolderChannels);

      const seenUnassignedKeys = new Set<string>();
      const dedupedUnassigned: ChannelProject[] = [];
      (unassignedRes || [])
        .filter((c: ChannelProject) => !c.folder_id)
        .forEach((c: ChannelProject) => {
          const cid = canonicalChannelId(c.channel_id, c.workspace_id || c.workspace_name, c.channel_name);
          const authWs = getAuthoritativeWorkspace(cid, c.workspace_id, c.workspace_name);
          const key = cid.toUpperCase();
          if (!key || seenUnassignedKeys.has(key)) return;
          seenUnassignedKeys.add(key);

          let cname = c.channel_name;
          if (AUTHORITATIVE_CHANNEL_NAMES[key]) {
            cname = AUTHORITATIVE_CHANNEL_NAMES[key];
          }
          dedupedUnassigned.push({
            ...c,
            channel_id: cid,
            channel_name: cname,
            workspace_id: authWs.id,
            workspace_name: authWs.name,
          });
        });
      setUnassignedChannels(dedupedUnassigned);
    } catch (err: any) {
      console.error("Error loading folder details:", err);
      setFeedback({ type: "error", message: err?.message || "Failed to load folder details" });
    } finally {
      setLoading(false);
    }
  }, [folderId]);

  useEffect(() => {
    if (typeof window !== "undefined") {
      try {
        const rawUser = sessionStorage.getItem("jts_user");
        const u = JSON.parse(rawUser || "{}");
        const savedSim = sessionStorage.getItem("jts_simulated_role");
        const r = savedSim || u.role || "jts_admin";
        setUserRole(r);
        if ((r === "client_admin" || r === "client_standard") && u.client_folder_id) {
          if (String(u.client_folder_id) !== String(folderId)) {
            window.location.href = `/folders/${u.client_folder_id}`;
            return;
          }
        }
      } catch {}
    }
    loadData();
  }, [loadData, folderId]);

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
      {isMasterAdmin && (
        <Link href="/folders" className="inline-flex items-center gap-1.5 text-sm font-medium text-gray-500 hover:text-gray-800 transition w-fit">
          <ArrowLeft className="h-4 w-4" />
          <span>All client folders</span>
        </Link>
      )}

      <PageHeader
        icon={Folder}
        title={folder?.name || (loading ? "Loading..." : `Folder #${folderId}`)}
        description={folder?.description || "This client's Slack channels, messages and AI key settings."}
        actions={
          <>
            {isMasterAdmin && (
              <button
                onClick={handleSyncSlack}
                disabled={syncing || loading}
                className={btn.secondary}
                title="Find every Slack channel and DM the bot has been added to"
              >
                <RefreshCw className={`h-3.5 w-3.5 ${syncing ? "animate-spin" : ""}`} />
                <span>{syncing ? "Checking Slack..." : "Find Slack channels"}</span>
              </button>
            )}
            <button onClick={() => loadData()} disabled={loading} className={btn.secondary}>
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

      {/* Client's own Anthropic key vs. billed JTS key */}
      {folderId && (
        <ClientApiKeyCard folderId={folderId} canEdit={isMasterAdmin || userRole === "client_admin"} />
      )}

      {/* Client's own GitHub (GitHub App connection) */}
      {folderId && (
        <GithubConnectionCard folderId={folderId} canEdit={isMasterAdmin || userRole === "client_admin"} />
      )}

      {/* Websites the assistant published for this client */}
      {folderId && <WebsitesCard folderId={folderId} />}

      {/* Content Area */}
      <div className="space-y-8 flex-1">
        {loading ? (
          <LoadingState label="Loading channels..." />
        ) : (
          <>
            {/* Section 1: Channels Added to this Folder */}
            <div>
              <SectionTitle
                title={`Channels in this folder (${dedupedFolderChannels.length})`}
                description="Open a channel to read its conversation with the bot."
              />

              {dedupedFolderChannels.length === 0 ? (
                <EmptyState
                  icon={Hash}
                  title="No channels in this folder yet"
                  description={
                    isMasterAdmin
                      ? "Add a channel from the list below. The bot must already be invited to that channel in Slack."
                      : "Ask your JTS administrator to add your team's Slack channels here."
                  }
                />
              ) : (
                <div className="rounded-xl border border-gray-200 shadow-sm relative z-30 overflow-visible">
                  <table className="w-full text-left border-collapse overflow-visible">
                    <thead className="bg-[#088ADA] text-white text-xs uppercase tracking-wider sticky top-0 z-10 shadow-sm border-b border-gray-300">
                      <tr>
                        <th className="p-3 font-semibold bg-[#088ADA] text-white">Channel or DM</th>
                        <th className="p-3 font-semibold text-center bg-[#088ADA] text-white">Type</th>
                        <th className="p-3 font-semibold text-right bg-[#088ADA] text-white">Actions</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-200">
                      {dedupedFolderChannels.map((channel, idx) => {
                        const isDm = isDmChannel(channel.channel_name, channel.channel_id);
                        const channelHref = `/folders/${folderId}/channels/${encodeURIComponent(channel.channel_id)}`;
                        return (
                          <tr key={channel.channel_id} className={`transition hover:bg-gray-200 ${idx % 2 === 0 ? "bg-white" : "bg-[#ededed]"} ${activeToolsDropdown === channel.channel_id ? "relative z-50" : ""}`}>
                            <td className="p-3">
                              <div className="flex items-center gap-2.5">
                                <div className="h-7 w-7 rounded-lg bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA] shrink-0">
                                  {isDm ? <AtSign className="h-3.5 w-3.5" /> : <Hash className="h-3.5 w-3.5" />}
                                </div>
                                <div>
                                  <Link href={channelHref} className="text-sm font-semibold text-gray-800 hover:text-[#088ADA] hover:underline flex items-center gap-1.5">
                                    <span>{channel.channel_name}</span>
                                  </Link>
                                  <div className="font-mono text-[10px] text-gray-400">{channel.channel_id}</div>
                                </div>
                              </div>
                            </td>
                            <td className="p-3 text-center">
                              <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${isDm ? "bg-gray-100 text-[#088ADA]" : "bg-emerald-50 text-emerald-600 border border-emerald-200"}`}>
                                {isDm ? "DM" : "Channel"}
                              </span>
                            </td>
                            <td className="p-3 text-right flex items-center justify-end gap-2">
                              {isMasterAdmin && (
                                <div className="relative inline-block tools-dropdown-container">
                                  <button
                                    type="button"
                                    onClick={() =>
                                      setActiveToolsDropdown((prev) =>
                                        prev === channel.channel_id ? null : channel.channel_id
                                      )
                                    }
                                    className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold transition border shadow-xs ${
                                      activeToolsDropdown === channel.channel_id
                                        ? "bg-[#0778bd] text-white border-[#0778bd] ring-2 ring-[#088ADA]/30"
                                        : "bg-[#088ADA] hover:bg-[#0778bd] text-white border-[#088ADA]"
                                    }`}
                                    title="Add or configure tools for this channel"
                                  >
                                    <Wrench className="h-3.5 w-3.5 text-white" />
                                    <span>Tools</span>
                                    <ChevronDown
                                      className={`h-3 w-3 text-white transition-transform duration-200 ${
                                        activeToolsDropdown === channel.channel_id ? "rotate-180" : ""
                                      }`}
                                    />
                                  </button>

                                  {/* Dropdown Menu */}
                                  {activeToolsDropdown === channel.channel_id && (
                                    <div className="absolute right-0 mt-1.5 w-64 bg-white rounded-xl shadow-2xl border border-gray-200 py-1 z-50 animate-in fade-in slide-in-from-top-1 duration-150 text-left">
                                      <div className="px-3 py-1.5 border-b border-gray-100 flex items-center justify-between">
                                        <span className="text-[10px] font-bold text-gray-500 uppercase tracking-wider">
                                          Tools (coming soon)
                                        </span>
                                        <span className="text-[10px] text-gray-400 font-mono">
                                          #{channel.channel_name}
                                        </span>
                                      </div>

                                      <div className="max-h-64 overflow-y-auto py-1">
                                        {AVAILABLE_TOOLS.map((tool) => {
                                          const ToolIcon = tool.icon;
                                          return (
                                            <button
                                              key={tool.id}
                                              type="button"
                                              onClick={() => {
                                                setActiveToolsDropdown(null);
                                                setToolKeyFields({});
                                                setToolModalFeedback(null);
                                                setActiveToolModal({
                                                  channel,
                                                  tool,
                                                });
                                              }}
                                              className="w-full px-3 py-2 text-left hover:bg-blue-50/70 flex items-center justify-between transition group"
                                            >
                                              <div className="flex items-center gap-2.5 min-w-0">
                                                <div className="h-7 w-7 rounded-lg bg-gray-100 group-hover:bg-blue-100/60 flex items-center justify-center text-gray-700 group-hover:text-[#088ADA] shrink-0 transition">
                                                  <ToolIcon className="h-3.5 w-3.5" />
                                                </div>
                                                <div className="min-w-0">
                                                  <div className="text-xs font-semibold text-gray-800 group-hover:text-[#088ADA] truncate">
                                                    {tool.name}
                                                  </div>
                                                  <div className="text-[10px] text-gray-400 truncate">
                                                    {tool.desc}
                                                  </div>
                                                </div>
                                              </div>
                                              <span
                                                className={`text-[9px] font-semibold px-1.5 py-0.5 rounded border shrink-0 ${tool.badgeColor}`}
                                              >
                                                {tool.badge}
                                              </span>
                                            </button>
                                          );
                                        })}
                                      </div>
                                    </div>
                                  )}
                                </div>
                              )}
                              <Link
                                href={channelHref}
                                className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-[#088ADA]/10 text-[#088ADA] hover:bg-[#088ADA] hover:text-white text-xs font-medium transition"
                                title="View live messages in this channel"
                              >
                                <MessageSquare className="h-3.5 w-3.5" />
                                <span>Open chat</span>
                              </Link>
                              {isMasterAdmin && (
                                <button
                                  onClick={() => handleRemoveChannelFromFolder(channel)}
                                  className="p-1.5 text-gray-500 hover:text-rose-600 hover:bg-rose-100 rounded-lg transition"
                                  title="Remove from this folder"
                                >
                                  <Trash2 className="h-3.5 w-3.5" />
                                </button>
                              )}
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
            {isMasterAdmin && (
              <div className="pt-6 border-t border-gray-200">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 mb-4">
                  <div>
                    <h2 className="text-sm font-semibold text-gray-800">
                      Add a channel to this folder ({filteredUnassignedChannels.length} available)
                    </h2>
                    <p className="text-xs text-gray-500 mt-0.5">
                      Slack channels and DMs the bot is in that don&apos;t belong to any client yet.
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
                        placeholder="Search channels"
                        className="pl-8 pr-3 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-gray-800 text-xs placeholder-gray-400 focus:outline-none focus:border-[#088ADA] transition w-44 sm:w-56"
                      />
                    </div>
                    <button
                      onClick={handleSyncSlack}
                      disabled={syncing}
                      className="flex items-center gap-1 px-3 py-1.5 rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-medium border border-gray-200 transition shrink-0"
                      title="Check Slack for channels the bot was recently added to"
                    >
                      <RefreshCw className={`h-3.5 w-3.5 ${syncing ? "animate-spin text-[#088ADA]" : ""}`} />
                      <span>Refresh list</span>
                    </button>
                  </div>
                </div>

                {filteredUnassignedChannels.length === 0 ? (
                  <div className="p-6 rounded-2xl border border-gray-200 bg-gray-50 flex flex-col sm:flex-row items-center justify-between gap-4 text-xs text-gray-500">
                    <div className="flex items-center gap-2.5">
                      <Check className="h-4 w-4 text-emerald-600 shrink-0" />
                      <span>
                        {unassignedSearchQuery
                          ? "No channels match your search."
                          : "Every channel the bot is in already belongs to a client."}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-gray-500">Missing a channel? Invite the bot to it in Slack, then</span>
                      <button onClick={handleSyncSlack} disabled={syncing} className={btn.secondary}>
                        Check again
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="overflow-x-auto rounded-xl border border-gray-200 shadow-sm">
                    <table className="w-full text-left border-collapse">
                      <thead className="bg-[#088ADA] text-white text-xs uppercase tracking-wider sticky top-0 z-20 shadow-sm border-b border-gray-300">
                        <tr>
                          <th className="p-3 font-semibold bg-[#088ADA] text-white">Workspace ID</th>
                          <th className="p-3 font-semibold bg-[#088ADA] text-white">Slack workspace</th>
                          <th className="p-3 font-semibold bg-[#088ADA] text-white">Channel or DM</th>
                          <th className="p-3 font-semibold text-center bg-[#088ADA] text-white">Type</th>
                          <th className="p-3 font-semibold text-right bg-[#088ADA] text-white">Actions</th>
                        </tr>
                      </thead>
                      <tbody className="divide-y divide-gray-200">
                        {filteredUnassignedChannels.map((channel, idx) => {
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
            )}
          </>
        )}
      </div>

      {/* Tool Keys & Configuration Modal */}
      {activeToolModal && (
        <div className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 overflow-y-auto">
          <div className="bg-white rounded-2xl max-w-lg w-full p-6 shadow-2xl border border-gray-200 space-y-4 max-h-[90vh] overflow-y-auto my-auto animate-in fade-in zoom-in-95 duration-200">
            {/* Modal Header */}
            <div className="flex items-start justify-between border-b pb-4 border-gray-100">
              <div className="flex items-center gap-3">
                <div className="h-10 w-10 rounded-xl bg-blue-50 border border-blue-200/70 flex items-center justify-center text-[#088ADA] shrink-0">
                  {(() => {
                    const ModalIcon = activeToolModal.tool.icon;
                    return <ModalIcon className="h-5 w-5" />;
                  })()}
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="font-semibold text-gray-900 text-base">
                      {activeToolModal.tool.name} for this channel
                    </h3>
                    <ComingSoonBadge />
                  </div>
                  <p className="text-xs text-gray-500 mt-0.5">
                    Connect {activeToolModal.tool.name} to <span className="font-semibold text-gray-800">{activeToolModal.channel.channel_name}</span>
                  </p>
                </div>
              </div>

              <button
                type="button"
                onClick={() => {
                  setActiveToolModal(null);
                  setToolKeyFields({});
                  setToolModalFeedback(null);
                }}
                className="text-gray-400 hover:text-gray-600 p-1 rounded-lg hover:bg-gray-100 transition"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            {/* Modal Feedback Banner */}
            {toolModalFeedback && (
              <div
                className={`p-3 rounded-xl border text-xs flex items-start gap-2.5 ${
                  toolModalFeedback.type === "success"
                    ? "bg-emerald-50 border-emerald-200 text-emerald-800"
                    : "bg-rose-50 border-rose-200 text-rose-800"
                }`}
              >
                {toolModalFeedback.type === "success" ? (
                  <CheckCircle2 className="h-4 w-4 text-emerald-600 shrink-0 mt-0.5" />
                ) : (
                  <AlertCircle className="h-4 w-4 text-rose-600 shrink-0 mt-0.5" />
                )}
                <span>{toolModalFeedback.message}</span>
              </div>
            )}

            {/* Modal Form */}
            <Alert type="info" title="Not available yet">
              Per-channel {activeToolModal.tool.name} settings can&apos;t be saved yet. For now the bot uses the
              system-wide {activeToolModal.tool.name} connection set up by your JTS administrator.
            </Alert>

            <form onSubmit={(e) => e.preventDefault()} className="space-y-3.5 text-xs opacity-60">

              <div className="p-3 bg-gray-50 rounded-xl border border-gray-200 text-gray-600 flex items-center justify-between font-mono text-[11px]">
                <span className="flex items-center gap-1.5">
                  <KeyRound className="h-3.5 w-3.5 text-[#088ADA]" />
                  Channel ID:
                </span>
                <span className="font-semibold text-gray-800">{activeToolModal.channel.channel_id}</span>
              </div>

              {activeToolModal.tool.fields.map((field) => (
                <div key={field.key} className="space-y-1">
                  <label className="block text-gray-700 font-semibold">
                    {field.label} {field.required && <span className="text-rose-500">*</span>}
                  </label>
                  <input
                    type={field.type}
                    required={field.required}
                    disabled
                    placeholder={field.placeholder}
                    value={toolKeyFields[field.key] || ""}
                    onChange={(e) =>
                      setToolKeyFields((prev) => ({
                        ...prev,
                        [field.key]: e.target.value,
                      }))
                    }
                    className="w-full p-2.5 border border-gray-300 rounded-lg text-gray-800 focus:outline-none focus:border-[#088ADA] focus:ring-1 focus:ring-[#088ADA] font-mono text-xs"
                  />
                </div>
              ))}

              <div className="pt-3 border-t border-gray-100 flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setActiveToolModal(null);
                    setToolKeyFields({});
                    setToolModalFeedback(null);
                  }}
                  className={btn.secondary}
                >
                  Close
                </button>
                <button type="submit" disabled className={btn.primary}>
                  <ShieldCheck className="h-3.5 w-3.5" />
                  <span>Save (coming soon)</span>
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
