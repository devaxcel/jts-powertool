'use client';

import { useEffect, useState, useCallback, useRef } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  ArrowLeft,
  RefreshCw,
  Hash,
  AtSign,
  MessageSquare,
  Bot,
  Search,
  Loader2,
  AlertCircle,
  Zap,
  DollarSign,
  Clock,
  X,
  GitBranch,
  KeyRound,
  ShieldCheck,
  Eye,
  EyeOff,
  Check,
  CheckCircle2,
} from "lucide-react";
import { fetchChannelMessages, fetchFolder, fetchBillingSummary, canonicalChannelId } from "@/lib/api";
import { ConversationMessage, FolderDetails, formatLocalDateTime } from "@/lib/types";

export default function ChannelMessagesPage() {
  const params = useParams();
  const folderId = params?.folderId as string;
  const channelId = params?.channelId as string;

  const [folder, setFolder] = useState<FolderDetails | null>(null);
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [resolvedChannelName, setResolvedChannelName] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [autoRefresh, setAutoRefresh] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [showTelemetryModal, setShowTelemetryModal] = useState(false);
  const [channelTelemetry, setChannelTelemetry] = useState<{
    calls: number;
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    total_cost_usd: number;
  } | null>(null);

  // RBAC state: Only Master Admin can configure GitHub per channel
  const [userRole, setUserRole] = useState<string>("jts_admin");
  const isMasterAdmin = !userRole || userRole === "admin" || userRole === "jts_admin";

  // GitHub Channel Configuration Modal State
  const [showGithubModal, setShowGithubModal] = useState(false);
  const [githubToken, setGithubToken] = useState("");
  const [githubRepo, setGithubRepo] = useState("");
  const [showGithubToken, setShowGithubToken] = useState(false);
  const [savingGithub, setSavingGithub] = useState(false);
  const [githubFeedback, setGithubFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [isGithubConfigured, setIsGithubConfigured] = useState(false);

  const chatBottomRef = useRef<HTMLDivElement>(null);

  const loadChannelData = useCallback(
    async (isBackground = false) => {
      if (!channelId) return;
      const targetChannelId = canonicalChannelId(channelId);
      if (!isBackground) setRefreshing(true);
      try {
        const [msgRes, folderRes, billingRes] = await Promise.all([
          fetchChannelMessages(targetChannelId),
          folderId ? fetchFolder(folderId).catch(() => null) : Promise.resolve(null),
          fetchBillingSummary().catch(() => null),
        ]);
        setMessages(msgRes.messages || []);
        if (msgRes.channel_name && msgRes.channel_name !== channelId) {
          setResolvedChannelName(msgRes.channel_name);
        }
        if (folderRes?.folder) {
          setFolder(folderRes.folder);
        }

        // Consolidate billing channels using the exact same logic and variables as the Billing & Token page
        let matchedBilling = null;
        if (billingRes && Array.isArray(billingRes.by_channel)) {
          const chanMap = new Map<string, any>();
          (billingRes.by_channel || []).forEach((item: any) => {
            const rawWs = item.workspace_id || item.workspace_name || "";
            const cid = canonicalChannelId(item.channel_id, rawWs, item.channel_name);
            const cleanCid = cid.toUpperCase();
            if (!cleanCid) return;

            if (!chanMap.has(cleanCid)) {
              chanMap.set(cleanCid, { ...item, channel_id: cid });
            } else {
              const existing = chanMap.get(cleanCid);
              existing.calls = (existing.calls || 0) + (item.calls || 0);
              existing.input_tokens = (existing.input_tokens || 0) + (item.input_tokens || 0);
              existing.output_tokens = (existing.output_tokens || 0) + (item.output_tokens || 0);
              existing.total_tokens = (existing.total_tokens || 0) + (item.total_tokens || 0);
              existing.total_cost_usd = Number(((existing.total_cost_usd || 0) + (item.total_cost_usd || 0)).toFixed(6));
            }
          });

          const targetCanonical = canonicalChannelId(channelId).toUpperCase();
          const targetRaw = (channelId || "").trim().toUpperCase();
          const targetName = (msgRes.channel_name || resolvedChannelName || "").replace(/^[#@]/, "").toLowerCase();

          matchedBilling = chanMap.get(targetCanonical) || chanMap.get(targetRaw);
          if (!matchedBilling && targetName) {
            matchedBilling = Array.from(chanMap.values()).find((c: any) => {
              const cName = (c.channel_name || "").replace(/^[#@]/, "").toLowerCase();
              return cName && cName === targetName;
            });
          }
        }

        if (matchedBilling) {
          setChannelTelemetry({
            calls: matchedBilling.calls || 0,
            input_tokens: matchedBilling.input_tokens || 0,
            output_tokens: matchedBilling.output_tokens || 0,
            total_tokens: matchedBilling.total_tokens || 0,
            total_cost_usd: matchedBilling.total_cost_usd || 0.0,
          });
        } else if (msgRes.telemetry) {
          setChannelTelemetry(msgRes.telemetry);
        }

        setError(null);
      } catch (err: any) {
        console.error("Error fetching channel messages:", err);
        setError(err?.message || "Failed to load channel messages");
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [channelId, folderId]
  );

  useEffect(() => {
    // Client-side RBAC check for client_admin
    if (typeof window !== "undefined") {
      try {
        const rawUser = sessionStorage.getItem("jts_user");
        const u = JSON.parse(rawUser || "{}");
        const savedSim = sessionStorage.getItem("jts_simulated_role");
        const role = savedSim || u.role || "jts_admin";
        setUserRole(role);
        if ((role === "client_admin" || role === "client_standard") && u.client_folder_id) {
          if (String(u.client_folder_id) !== String(folderId)) {
            window.location.href = `/folders/${u.client_folder_id}`;
            return;
          }
        }

        // Load channel GitHub config
        const savedGh = localStorage.getItem(`jts_channel_gh_${channelId}`);
        if (savedGh) {
          const parsed = JSON.parse(savedGh);
          setGithubToken(parsed.token || "");
          setGithubRepo(parsed.repo || "");
          setIsGithubConfigured(Boolean(parsed.token || parsed.repo));
        }
      } catch {}
    }
    loadChannelData();
  }, [loadChannelData, folderId, channelId]);

  const handleSaveGithub = (e: React.FormEvent) => {
    e.preventDefault();
    setSavingGithub(true);
    try {
      const cleanRepo = githubRepo.trim();
      const cleanToken = githubToken.trim();
      if (!cleanRepo && !cleanToken) {
        localStorage.removeItem(`jts_channel_gh_${channelId}`);
        setIsGithubConfigured(false);
        setGithubFeedback({ type: "success", message: "Channel GitHub configuration cleared." });
      } else {
        localStorage.setItem(`jts_channel_gh_${channelId}`, JSON.stringify({ token: cleanToken, repo: cleanRepo }));
        setIsGithubConfigured(true);
        setGithubFeedback({
          type: "success",
          message: `GitHub configuration saved for ${displayChannelName}! Repository: ${cleanRepo || "Default"}`,
        });
      }
    } catch (err: any) {
      setGithubFeedback({ type: "error", message: err?.message || "Failed to save configuration" });
    } finally {
      setSavingGithub(false);
    }
  };

  const handleRemoveGithub = () => {
    if (!confirm("Are you sure you want to remove the custom GitHub settings for this channel?")) return;
    setGithubToken("");
    setGithubRepo("");
    setIsGithubConfigured(false);
    localStorage.removeItem(`jts_channel_gh_${channelId}`);
    setGithubFeedback({ type: "success", message: "GitHub settings removed. Channel will use system defaults." });
  };

  // Auto scroll to bottom when initial loading finishes
  useEffect(() => {
    if (!loading && messages.length > 0) {
      chatBottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [loading, messages.length]);

  // Auto refresh interval (5s)
  useEffect(() => {
    if (!autoRefresh) return;
    const interval = setInterval(() => {
      loadChannelData(true);
    }, 5000);
    return () => clearInterval(interval);
  }, [autoRefresh, loadChannelData]);

  const matchedChannel = folder?.channels?.find(
    (c) => c.channel_id.toLowerCase() === channelId.toLowerCase() || (c.channel_name && c.channel_name.toLowerCase() === channelId.toLowerCase())
  );
  const rawChannelName = resolvedChannelName || matchedChannel?.channel_name || channelId;
  const isDm = channelId.startsWith("D") || rawChannelName.startsWith("@");
  
  // Format channel display name so it shows friendly channel name (e.g. #agents_working_projects)
  const displayChannelName = isDm
    ? (rawChannelName.startsWith("@") ? rawChannelName : `@${rawChannelName}`)
    : (rawChannelName.startsWith("#") ? rawChannelName : rawChannelName.startsWith("C0") ? channelId : `#${rawChannelName}`);

  const filteredMessages = messages.filter((m) => {
    if (!searchQuery.trim()) return true;
    const q = searchQuery.toLowerCase();
    return (
      (m.content && m.content.toLowerCase().includes(q)) ||
      (m.user_name && m.user_name.toLowerCase().includes(q)) ||
      (m.user_id && m.user_id.toLowerCase().includes(q))
    );
  });

  // Calculate channel-wide bot telemetry totals
  const botMessages = messages.filter((msg) => {
    return (
      msg.role === "assistant" ||
      msg.user_id === "bot" ||
      (msg.user_name && (msg.user_name.includes("Assistant") || msg.user_name.includes("bot") || msg.user_name.includes("Agent")))
    );
  });

  const channelTotalBotMessages = channelTelemetry ? channelTelemetry.calls : botMessages.length;
  const channelTotalInputTokens = channelTelemetry ? channelTelemetry.input_tokens : botMessages.reduce((sum, m) => sum + (m.input_tokens || 0), 0);
  const channelTotalOutputTokens = channelTelemetry ? channelTelemetry.output_tokens : botMessages.reduce((sum, m) => sum + (m.output_tokens || 0), 0);
  const channelTotalTokens = channelTelemetry ? channelTelemetry.total_tokens : botMessages.reduce((sum, m) => sum + (m.total_tokens || 0), 0);
  const channelTotalCostUsd = channelTelemetry ? channelTelemetry.total_cost_usd : botMessages.reduce((sum, m) => sum + (m.cost_usd || 0.0), 0);

  return (
    <div className="max-w-7xl mx-auto flex flex-col h-[calc(100vh-7.5rem)] lg:h-[calc(100vh-8.5rem)] overflow-hidden space-y-3">
      {/* Fixed Header Container (Breadcrumb, Actions, & Channel Banner) */}
      <div className="shrink-0 space-y-3 z-10">
        {/* Top Header / Breadcrumb */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5">
        <div className="flex items-center gap-2">
          <Link
            href={`/folders/${folderId}`}
            className="inline-flex items-center gap-1.5 text-xs font-semibold text-gray-600 hover:text-gray-900 bg-gray-100 hover:bg-gray-200 border border-gray-200 px-3 py-1.5 rounded-lg transition"
          >
            <ArrowLeft className="h-3.5 w-3.5" />
            <span>Back to Folder ({folder?.name || `#${folderId}`})</span>
          </Link>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Telemetry Modal Button before Live Sync Active */}
          <button
            onClick={() => setShowTelemetryModal(true)}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gradient-to-r from-emerald-50 to-teal-50 hover:from-emerald-100 hover:to-teal-100 text-emerald-800 border border-emerald-200 text-xs font-semibold shadow-xs transition cursor-pointer"
            title="Click to view total tokens and API cost of all bot messages in this channel"
          >
            <Zap className="h-3.5 w-3.5 text-emerald-600 animate-pulse" />
            <span>Total Cost & Tokens</span>
          </button>

          <button
            onClick={() => setAutoRefresh(!autoRefresh)}
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium border transition ${
              autoRefresh
                ? "bg-emerald-50 text-emerald-700 border-emerald-200"
                : "bg-gray-100 text-gray-600 border-gray-200"
            }`}
          >
            <span className={`h-2 w-2 rounded-full ${autoRefresh ? "bg-emerald-500 animate-pulse" : "bg-gray-400"}`} />
            <span>{autoRefresh ? "Live Sync Active" : "Live Sync Paused"}</span>
          </button>

          <button
            onClick={() => loadChannelData()}
            disabled={refreshing}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-medium border border-gray-200 transition disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin text-[#088ADA]" : ""}`} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* Channel Header Banner */}
      <div className="bg-white border border-gray-200 rounded-xl p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 shadow-sm shrink-0">
        <div className="flex items-center gap-3">
          <div className="h-10 w-10 rounded-xl bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA] shrink-0">
            {isDm ? <AtSign className="h-5 w-5" /> : <Hash className="h-5 w-5" />}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-lg font-bold text-gray-800">{displayChannelName}</h1>
              <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium ${isDm ? "bg-gray-100 text-[#088ADA]" : "bg-emerald-50 text-emerald-600 border border-emerald-200"}`}>
                {isDm ? "Direct Message" : "Slack Channel"}
              </span>
            </div>
            <p className="text-xs text-gray-500 font-mono mt-0.5">
              Channel ID: {channelId} | Folder: {folder?.name || `#${folderId}`}
            </p>
          </div>
        </div>

        {/* Search bar inside chat header */}
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="absolute left-3 top-2.5 h-3.5 w-3.5 text-gray-400" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search chat history..."
              className="pl-9 pr-3 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs text-gray-800 placeholder-gray-400 focus:outline-none focus:border-[#088ADA] transition w-56 sm:w-64"
            />
          </div>
          <span className="text-xs text-gray-500 bg-gray-100 px-2.5 py-1.5 rounded-lg border border-gray-200 font-medium shrink-0">
            {filteredMessages.length} {filteredMessages.length === 1 ? "Message" : "Messages"}
          </span>
        </div>
      </div>

      {/* Error display */}
      {error && (
        <div className="p-3 bg-rose-50 border border-rose-200 text-rose-700 rounded-xl text-xs flex items-center gap-2 shrink-0">
          <AlertCircle className="h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}
      </div>

      {/* Chat Messages Main Box (WhatsApp / Slack style stream) */}
      <div className="flex-1 min-h-0 bg-[#f4f6f8] border border-gray-200 rounded-2xl overflow-y-auto p-4 space-y-4 shadow-inner">
        {loading ? (
          <div className="h-full flex flex-col items-center justify-center gap-3 text-gray-500">
            <Loader2 className="h-8 w-8 animate-spin text-[#088ADA]" />
            <p className="text-sm font-medium">Loading channel messages...</p>
          </div>
        ) : filteredMessages.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center text-center p-6 text-gray-400">
            <div className="h-12 w-12 rounded-2xl bg-gray-200/60 flex items-center justify-center mb-3">
              <MessageSquare className="h-6 w-6 text-gray-400" />
            </div>
            <p className="text-sm font-semibold text-gray-600">
              {searchQuery ? "No messages match your search filter." : "No messages recorded for this channel yet."}
            </p>
            <p className="text-xs text-gray-400 mt-1 max-w-sm">
              Any conversation messages sent by team members or the JTS Assistant in Slack will appear here automatically.
            </p>
          </div>
        ) : (
          filteredMessages.map((msg, idx) => {
            const isBot = msg.role === "assistant" || (msg.user_name && msg.user_name.includes("Assistant"));
            const senderName = msg.user_name || (isBot ? "JTS Assistant" : msg.user_id || "Team Member");

            return (
              <div
                key={msg.id || idx}
                className="flex gap-3 max-w-3xl mr-auto"
              >
                {/* Sender Avatar */}
                <div
                  className={`h-9 w-9 rounded-xl flex items-center justify-center text-white font-bold text-xs shrink-0 shadow-sm ${
                    isBot ? "bg-gradient-to-tr from-[#088ADA] to-cyan-500" : "bg-gradient-to-tr from-gray-700 to-gray-900"
                  }`}
                >
                  {isBot ? <Bot className="h-5 w-5" /> : senderName.charAt(0).toUpperCase()}
                </div>

                {/* Message Content Bubble */}
                <div
                  className={`rounded-2xl p-4 shadow-sm border text-sm max-w-full overflow-hidden ${
                    isBot
                      ? "bg-white border-sky-200 text-gray-800"
                      : "bg-white border-gray-200 text-gray-800"
                  }`}
                >
                  {/* Sender Header */}
                  <div className="flex items-center gap-2 mb-1.5 pb-1 border-b border-gray-100 flex-wrap">
                    <span className="font-bold text-xs text-gray-900">{senderName}</span>
                    <span
                      className={`text-[10px] px-2 py-0.2 rounded-full font-semibold ${
                        isBot
                          ? "bg-sky-100 text-[#088ADA] border border-sky-200"
                          : "bg-gray-100 text-gray-600 border border-gray-200"
                      }`}
                    >
                      {isBot ? "JTS Assistant" : "User"}
                    </span>

                    {isBot ? (
                      <div className="flex items-center gap-2 ml-auto text-[10px] font-mono flex-wrap">
                        <span
                          className="px-2 py-0.5 rounded-md bg-emerald-50 text-emerald-700 border border-emerald-200 font-bold flex items-center gap-1"
                          title={`Tokens: ${(msg.input_tokens || 0).toLocaleString()} in / ${(msg.output_tokens || 0).toLocaleString()} out`}
                        >
                          <Zap className="h-3 w-3 text-emerald-600" />
                          <span>{(msg.total_tokens || 0).toLocaleString()} tokens</span>
                        </span>
                        <span className="px-2 py-0.5 rounded-md bg-blue-50 text-blue-700 border border-blue-200 font-bold flex items-center gap-1">
                          <DollarSign className="h-3 w-3 text-blue-600" />
                          <span>${(msg.cost_usd || 0).toFixed(6)}</span>
                        </span>
                        <span className="text-gray-400 flex items-center gap-1">
                          <Clock className="h-3 w-3 text-gray-300" />
                          {formatLocalDateTime(msg.created_at || msg.message_ts)}
                        </span>
                      </div>
                    ) : (
                      <span className="text-[10px] text-gray-400 ml-auto flex items-center gap-1 font-mono">
                        <Clock className="h-3 w-3 text-gray-300" />
                        {formatLocalDateTime(msg.created_at || msg.message_ts)}
                      </span>
                    )}
                  </div>

                  {/* Body Text */}
                  <div className="whitespace-pre-wrap break-words text-gray-700 font-sans text-xs leading-relaxed">
                    {msg.content}
                  </div>
                </div>
              </div>
            );
          })
        )}
        <div ref={chatBottomRef} />
      </div>

      {/* Footer Info */}
      <div className="flex items-center justify-between text-[11px] text-gray-400 px-2 shrink-0">
        <span>Showing conversation log synced from Slack via WebSocket / Event API</span>
        <span>Auto-refreshes every 5 seconds</span>
      </div>

      {/* Telemetry Pop-up Modal */}
      {showTelemetryModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/50 backdrop-blur-xs p-4 animate-in fade-in duration-200">
          <div className="bg-white rounded-2xl shadow-2xl border border-gray-200 w-full max-w-md overflow-hidden flex flex-col transform transition-all">
            {/* Modal Header */}
            <div className="px-5 py-4 border-b border-gray-100 flex items-center justify-between bg-gradient-to-r from-slate-50 to-gray-50">
              <div className="flex items-center gap-2.5">
                <div className="h-9 w-9 rounded-xl bg-emerald-100 text-emerald-700 flex items-center justify-center font-bold">
                  <Zap className="h-5 w-5 text-emerald-600" />
                </div>
                <div>
                  <h3 className="font-bold text-gray-900 text-sm">Channel Telemetry & Cost</h3>
                  <p className="text-[11px] text-gray-500 font-mono">
                    {displayChannelName} ({channelId})
                  </p>
                </div>
              </div>
              <button
                onClick={() => setShowTelemetryModal(false)}
                className="h-8 w-8 rounded-lg hover:bg-gray-200/70 flex items-center justify-center text-gray-500 hover:text-gray-800 transition cursor-pointer"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Modal Body */}
            <div className="p-5 space-y-4">
              {/* Summary Cards Grid */}
              <div className="grid grid-cols-2 gap-3">
                {/* Total Cost Card */}
                <div className="bg-gradient-to-br from-blue-50/80 to-indigo-50/80 border border-blue-200/80 rounded-xl p-3.5 flex flex-col justify-between">
                  <div className="flex items-center justify-between text-blue-700 text-xs font-semibold mb-1">
                    <span>Total API Cost</span>
                    <DollarSign className="h-4 w-4 text-blue-600" />
                  </div>
                  <div className="text-xl font-extrabold text-blue-950 font-mono">
                    ${channelTotalCostUsd.toFixed(6)}
                  </div>
                  <div className="text-[10px] text-blue-600 font-medium mt-1">
                    USD for {channelTotalBotMessages} bot replies
                  </div>
                </div>

                {/* Total Tokens Card */}
                <div className="bg-gradient-to-br from-emerald-50/80 to-teal-50/80 border border-emerald-200/80 rounded-xl p-3.5 flex flex-col justify-between">
                  <div className="flex items-center justify-between text-emerald-700 text-xs font-semibold mb-1">
                    <span>Total Tokens</span>
                    <Zap className="h-4 w-4 text-emerald-600" />
                  </div>
                  <div className="text-xl font-extrabold text-emerald-950 font-mono">
                    {channelTotalTokens.toLocaleString()}
                  </div>
                  <div className="text-[10px] text-emerald-600 font-medium mt-1">
                    Input & output combined
                  </div>
                </div>
              </div>

              {/* Detailed Token Breakdown Box */}
              <div className="bg-gray-50 rounded-xl border border-gray-200/80 p-3.5 space-y-2 text-xs text-gray-700">
                <div className="font-bold text-gray-900 pb-1.5 border-b border-gray-200 flex items-center justify-between">
                  <span>Usage & Token Breakdown</span>
                  <span className="text-[10px] font-mono text-gray-500 font-normal">
                    {channelTotalBotMessages} Bot {channelTotalBotMessages === 1 ? "Reply" : "Replies"}
                  </span>
                </div>

                <div className="flex justify-between items-center py-0.5 font-mono text-[11px]">
                  <span className="text-gray-500 flex items-center gap-1.5">
                    <span className="h-1.5 w-1.5 rounded-full bg-blue-500 inline-block" />
                    Input Tokens (Prompt Context):
                  </span>
                  <span className="font-semibold text-gray-900">{channelTotalInputTokens.toLocaleString()}</span>
                </div>

                <div className="flex justify-between items-center py-0.5 font-mono text-[11px]">
                  <span className="text-gray-500 flex items-center gap-1.5">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500 inline-block" />
                    Output Tokens (Generation):
                  </span>
                  <span className="font-semibold text-gray-900">{channelTotalOutputTokens.toLocaleString()}</span>
                </div>

                <div className="flex justify-between items-center py-0.5 font-mono text-[11px] pt-1.5 border-t border-gray-200">
                  <span className="text-gray-500">Average Tokens per Reply:</span>
                  <span className="font-semibold text-gray-800">
                    {channelTotalBotMessages > 0 ? Math.round(channelTotalTokens / channelTotalBotMessages).toLocaleString() : 0}
                  </span>
                </div>

                <div className="flex justify-between items-center py-0.5 font-mono text-[11px]">
                  <span className="text-gray-500">Average Cost per Reply:</span>
                  <span className="font-semibold text-gray-800">
                    ${channelTotalBotMessages > 0 ? (channelTotalCostUsd / channelTotalBotMessages).toFixed(6) : "0.000000"}
                  </span>
                </div>
              </div>
            </div>

            {/* Modal Footer */}
            <div className="px-5 py-3 bg-gray-50 border-t border-gray-100 flex justify-end">
              <button
                onClick={() => setShowTelemetryModal(false)}
                className="px-4 py-1.5 rounded-xl bg-gray-200 hover:bg-gray-300 text-gray-800 text-xs font-bold transition cursor-pointer"
              >
                Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* GitHub Channel Configuration Modal (Admin Only) */}
      {isMasterAdmin && showGithubModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs animate-in fade-in">
          <div className="bg-white border border-gray-200 rounded-2xl max-w-lg w-full shadow-2xl overflow-hidden animate-in zoom-in-95 duration-150">
            {/* Modal Header */}
            <div className="px-6 py-4.5 bg-gradient-to-r from-gray-900 via-slate-900 to-gray-900 text-white flex items-center justify-between">
              <div className="flex items-center gap-3">
                <div className="h-9 w-9 rounded-xl bg-white/10 border border-white/20 flex items-center justify-center text-[#088ADA]">
                  <GitBranch className="h-5 w-5" />
                </div>
                <div>
                  <div className="flex items-center gap-2">
                    <h3 className="font-bold text-sm text-white">GitHub Integration</h3>
                    <span className="text-[10px] px-2 py-0.5 rounded-full font-semibold bg-[#088ADA]/20 text-[#088ADA] border border-[#088ADA]/40">
                      Admin Only
                    </span>
                  </div>
                  <p className="text-xs text-gray-400 font-mono mt-0.5">
                    {displayChannelName} ({channelId})
                  </p>
                </div>
              </div>
              <button
                onClick={() => setShowGithubModal(false)}
                className="h-8 w-8 rounded-lg hover:bg-white/10 flex items-center justify-center text-gray-400 hover:text-white transition cursor-pointer"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            {/* Modal Body */}
            <form onSubmit={handleSaveGithub} className="p-6 space-y-4">
              {/* Informational Box */}
              <div className="bg-blue-50/70 border border-blue-200/80 rounded-xl p-3.5 flex items-start gap-2.5 text-xs text-blue-900">
                <ShieldCheck className="h-4 w-4 text-[#088ADA] shrink-0 mt-0.5" />
                <div className="space-y-1">
                  <p className="font-semibold text-blue-950">Fine-Grained Permission Enforcement</p>
                  <p className="text-blue-800 text-[11px] leading-relaxed">
                    Permissions (<strong>Read-Only</strong> or <strong>Read &amp; Write</strong>) are configured when generating your Personal Access Token on GitHub. The bot will strictly adhere to whatever access scopes are granted on that token.
                  </p>
                </div>
              </div>

              {/* Feedback Banner */}
              {githubFeedback && (
                <div
                  className={`flex items-center justify-between p-3 rounded-xl border text-xs ${
                    githubFeedback.type === "success"
                      ? "bg-emerald-50 border-emerald-200 text-emerald-700"
                      : "bg-rose-50 border-rose-200 text-rose-700"
                  }`}
                >
                  <div className="flex items-center gap-2">
                    {githubFeedback.type === "success" ? (
                      <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-600" />
                    ) : (
                      <AlertCircle className="h-4 w-4 shrink-0 text-rose-600" />
                    )}
                    <span>{githubFeedback.message}</span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setGithubFeedback(null)}
                    className="p-1 rounded hover:bg-black/5 text-gray-500"
                  >
                    <X className="h-3.5 w-3.5" />
                  </button>
                </div>
              )}

              {/* Field 1: GitHub Personal Access Token */}
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-gray-800 flex items-center justify-between">
                  <span className="flex items-center gap-1.5">
                    <KeyRound className="h-3.5 w-3.5 text-[#088ADA]" />
                    GitHub Personal Access Token (PAT)
                  </span>
                  <span className="text-[10px] text-gray-400 font-normal">Secret Key</span>
                </label>
                <div className="relative">
                  <input
                    type={showGithubToken ? "text" : "password"}
                    value={githubToken}
                    onChange={(e) => setGithubToken(e.target.value)}
                    placeholder="github_pat_11ABCD... or ghp_..."
                    className="w-full pl-3 pr-10 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono text-gray-900 placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:bg-white transition"
                  />
                  <button
                    type="button"
                    onClick={() => setShowGithubToken(!showGithubToken)}
                    className="absolute right-2.5 top-2 text-gray-400 hover:text-gray-600 p-0.5 rounded transition"
                    title={showGithubToken ? "Hide token" : "Show token"}
                  >
                    {showGithubToken ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
                <p className="text-[10px] text-gray-500">
                  Generate under <em>GitHub &rarr; Settings &rarr; Developer Settings &rarr; Personal Access Tokens</em>.
                </p>
              </div>

              {/* Field 2: Target Repository */}
              <div className="space-y-1.5">
                <label className="text-xs font-bold text-gray-800 flex items-center justify-between">
                  <span className="flex items-center gap-1.5">
                    <GitBranch className="h-3.5 w-3.5 text-[#088ADA]" />
                    Target Repository
                  </span>
                  <span className="text-[10px] text-gray-400 font-normal">owner/repo</span>
                </label>
                <div className="relative">
                  <input
                    type="text"
                    value={githubRepo}
                    onChange={(e) => setGithubRepo(e.target.value)}
                    placeholder="e.g. your-organization/repository-name"
                    className="w-full px-3 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs font-mono text-gray-900 placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:bg-white transition"
                  />
                </div>
                <p className="text-[10px] text-gray-500">
                  The specific GitHub repository that developers in this Slack channel can interact with.
                </p>
              </div>

              {/* Modal Footer Controls */}
              <div className="pt-3 border-t border-gray-100 flex items-center justify-between gap-2">
                <div>
                  {isGithubConfigured && (
                    <button
                      type="button"
                      onClick={handleRemoveGithub}
                      className="px-3 py-1.5 rounded-lg text-rose-600 hover:bg-rose-50 text-xs font-semibold transition"
                    >
                      Remove Config
                    </button>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setShowGithubModal(false)}
                    className="px-3.5 py-1.5 rounded-xl bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-semibold transition cursor-pointer"
                  >
                    Close
                  </button>
                  <button
                    type="submit"
                    disabled={savingGithub}
                    className="flex items-center gap-1.5 px-4 py-1.5 rounded-xl bg-[#088ADA] hover:bg-[#0779bf] text-white text-xs font-bold shadow-sm transition disabled:opacity-50 cursor-pointer"
                  >
                    {savingGithub ? (
                      <>
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        <span>Saving...</span>
                      </>
                    ) : (
                      <>
                        <Check className="h-3.5 w-3.5" />
                        <span>Save Settings</span>
                      </>
                    )}
                  </button>
                </div>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
