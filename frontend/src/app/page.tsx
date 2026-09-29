"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  ShieldAlert,
  CheckCircle2,
  Clock,
  Layers,
  MessagesSquare,
  Sparkles,
  ArrowUpRight,
  GitBranch,
  RefreshCw,
  Terminal,
  Activity,
  DollarSign,
} from "lucide-react";
import {
  fetchStats,
  fetchApprovals,
  submitApprovalAction,
  fetchUsageSummary,
  fetchFolder,
  fetchFolders,
  fetchChannelMessages,
} from "@/lib/api";
import { SystemStats, Approval, UsageSummary, isClientKeyMessage } from "@/lib/types";

export default function OverviewPage() {
  const [stats, setStats] = useState<SystemStats | null>(null);
  const [pendingApprovals, setPendingApprovals] = useState<Approval[]>([]);
  const [usage, setUsage] = useState<UsageSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [userRole, setUserRole] = useState<string>("jts_admin");

  useEffect(() => {
    if (typeof window !== "undefined") {
      try {
        const rawUser = sessionStorage.getItem("jts_user");
        const u = JSON.parse(rawUser || "{}");
        const savedSim = sessionStorage.getItem("jts_simulated_role");
        const r = savedSim || u.role || "jts_admin";
        setUserRole(r);
      } catch {}
    }
  }, []);

  const isMasterAdmin = !userRole || userRole === "admin" || userRole === "jts_admin";

  async function loadData() {
    try {
      // Determine active role and client folder
      let activeRole = "jts_admin";
      let clientFolderId: number | null = null;
      if (typeof window !== "undefined") {
        const rawUser = sessionStorage.getItem("jts_user");
        const u = JSON.parse(rawUser || "{}");
        const savedSim = sessionStorage.getItem("jts_simulated_role");
        activeRole = savedSim || u.role || "jts_admin";
        setUserRole(activeRole);
        clientFolderId = u.client_folder_id || 2;
      }

      if (activeRole === "client_admin" || activeRole === "client_standard") {
        // CLIENT ADMIN DASHBOARD (Strictly scoped to channels inside client folder)
        const targetFolderId = clientFolderId || 2;
        let folderChannels: any[] = [];
        try {
          const folderRes = await fetchFolder(targetFolderId);
          folderChannels = folderRes?.folder?.channels || folderRes?.channels || [];
        } catch {
          try {
            const allFolders = await fetchFolders();
            const found = allFolders.find((f) => String(f.id) === String(targetFolderId));
            if (found && (found as any).channels) {
              folderChannels = (found as any).channels;
            }
          } catch {}
        }

        const channelIdSet = new Set<string>();
        folderChannels.forEach((c) => {
          if (c.channel_id) {
            channelIdSet.add(c.channel_id.toLowerCase());
            channelIdSet.add(c.channel_id.replace(/^[@#]/, "").toLowerCase());
          }
          if (c.channel_name) {
            channelIdSet.add(c.channel_name.toLowerCase());
            channelIdSet.add(c.channel_name.replace(/^[@#]/, "").toLowerCase());
          }
        });

        // Sum tokens, cost, and memory history across each channel inside the client folder
        let folderTotalTokens = 0;
        let folderTotalCost = 0;
        let folderTotalMemoryTurns = 0;
        let folderBotCalls = 0;

        await Promise.all(
          folderChannels.map(async (ch) => {
            try {
              const msgRes = await fetchChannelMessages(ch.channel_id, 250);
              const msgs = msgRes.messages || [];
              folderTotalMemoryTurns += msgs.length;

              msgs.forEach((m) => {
                const isBot =
                  m.role === "assistant" ||
                  m.user_id === "bot" ||
                  (m.user_name && (m.user_name.includes("Assistant") || m.user_name.includes("bot") || m.user_name.includes("Agent")));

                if (isBot && !isClientKeyMessage(m)) {
                  folderBotCalls += 1;
                  let t = m.total_tokens || 0;
                  let c = m.cost_usd || 0;
                  if (!t || !c) {
                    const contentText = m.content || "";
                    const outToks = Math.max(20, Math.floor(contentText.length / 3.8));
                    const inToks = Math.max(250, Math.floor(outToks * 2.5));
                    t = t || (inToks + outToks);
                    c = c || Number(((inToks * 3.0 / 1000000) + (outToks * 15.0 / 1000000)).toFixed(6));
                  }
                  folderTotalTokens += t;
                  folderTotalCost += c;
                }
              });
            } catch (chErr) {
              console.warn(`Could not fetch channel ${ch.channel_id} messages:`, chErr);
            }
          })
        );

        // Fetch approvals and jobs for channel filtering
        const [allPendingAppr, allAppliedAppr, allStats] = await Promise.all([
          fetchApprovals("pending").catch(() => []),
          fetchApprovals("applied").catch(() => []),
          fetchStats().catch(() => null),
        ]);

        const matchesFolderChannel = (cId?: string) => {
          if (!cId) return false;
          const clean = cId.toLowerCase().replace(/^[@#]/, "");
          return channelIdSet.has(cId.toLowerCase()) || channelIdSet.has(clean);
        };

        const clientPending = allPendingAppr.filter((a) => matchesFolderChannel(a.channel_id));
        const clientApplied = allAppliedAppr.filter((a) => matchesFolderChannel(a.channel_id));

        const clientJobsTotal = clientApplied.length > 0 ? clientApplied.length : (allStats?.jobs?.total ? Math.min(allStats.jobs.total, folderTotalMemoryTurns) : 0);
        const clientJobsCompleted = clientApplied.length > 0 ? clientApplied.length : clientJobsTotal;

        setPendingApprovals(clientPending);
        setUsage({
          total_calls: folderBotCalls,
          total_input_tokens: Math.round(folderTotalTokens * 0.7),
          total_output_tokens: Math.round(folderTotalTokens * 0.3),
          total_tokens: folderTotalTokens,
          total_cost_usd: Number(folderTotalCost.toFixed(6)),
          active_channels_count: folderChannels.length,
          active_users_count: 1,
          by_channel: [],
          by_user: [],
          by_channel_user: [],
        });

        setStats({
          jobs: {
            total: clientJobsTotal,
            pending: 0,
            processing: 0,
            completed: clientJobsCompleted,
            failed: 0,
          },
          approvals: {
            total: clientPending.length + clientApplied.length,
            pending: clientPending.length,
            applied: clientApplied.length,
            rejected: 0,
            expired: 0,
          },
          conversations: folderTotalMemoryTurns,
          snapshots: 0,
        });

      } else {
        // JTS ADMIN DASHBOARD (Global System Overview - Untouched)
        const [s, a, u] = await Promise.all([
          fetchStats(),
          fetchApprovals("pending"),
          fetchUsageSummary().catch(() => null),
        ]);
        setStats(s);
        setPendingApprovals(a);
        if (u) setUsage(u);
      }
    } catch (err) {
      console.error("Error loading dashboard data:", err);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadData();
    const interval = setInterval(loadData, 8000);
    return () => clearInterval(interval);
  }, []);

  async function handleAction(id: string, action: "approve" | "reject") {
    setActionLoading(id);
    try {
      let currentUsername = "Admin User";
      if (typeof window !== "undefined") {
        const rawUser = sessionStorage.getItem("jts_user");
        const u = JSON.parse(rawUser || "{}");
        currentUsername = u.username || u.display_name || "Admin User";
      }
      await submitApprovalAction(id, action, currentUsername);
      await loadData();
    } catch (err) {
      console.error("Error submitting approval action:", err);
    } finally {
      setActionLoading(null);
    }
  }

  return (
    <div className="space-y-8 max-w-7xl mx-auto">
      {/* Welcome Banner */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 bg-[#088ADA] border border-blue-400 p-6 rounded-2xl shadow-md text-white">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold text-white tracking-tight">JTS PowerTool Hub</h1>
            <span className="text-xs bg-white/20 text-white px-2.5 py-0.5 rounded-full font-mono border border-white/30">
              {isMasterAdmin ? "Agent Replica" : "Client Portal"}
            </span>
          </div>
          <p className="text-xs sm:text-sm text-blue-50">
            {isMasterAdmin
              ? "Real-time human-in-the-loop approvals, Claude telemetry streams, and GitHub MCP automation."
              : "Real-time human-in-the-loop approvals, channel activity, and bot performance telemetry."}
          </p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => {
              setLoading(true);
              loadData();
            }}
            className="flex items-center gap-2 px-3.5 py-2 rounded-xl bg-white/10 hover:bg-white/20 text-white text-xs font-medium border border-white/30 transition"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            <span>Refresh</span>
          </button>
          <Link
            href="/approvals"
            className="flex items-center gap-2 px-4 py-2 rounded-xl bg-white hover:bg-gray-100 text-gray-800 text-xs font-semibold shadow-md transition"
          >
            <ShieldAlert className="h-3.5 w-3.5 text-[#088ADA]" />
            <span>Review Approvals</span>
          </Link>
        </div>
      </div>

      {/* KPI Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
        {/* API Token Cost */}
        <div className="bg-white border border-gray-200 p-5 rounded-2xl space-y-3 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-gray-600">Total Token Cost</span>
            <div className="p-2 rounded-lg bg-emerald-50 text-emerald-600 border border-emerald-200">
              <DollarSign className="h-4 w-4" />
            </div>
          </div>
          <div>
            <div className="text-2xl font-bold text-gray-900 font-mono">
              ${usage ? usage.total_cost_usd.toFixed(4) : "0.0000"}
            </div>
            <p className="text-xs text-gray-500 mt-1 flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500"></span>
              {usage ? `${usage.total_tokens.toLocaleString()} tokens used` : "Calculated per API call"}
            </p>
          </div>
        </div>
        {/* Pending Approvals */}
        <div className="bg-white border border-gray-200 p-5 rounded-2xl space-y-3 relative overflow-hidden shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-gray-600">Pending Approvals</span>
            <div className="p-2 rounded-lg bg-sky-50 text-[#088ADA] border border-sky-200">
              <Clock className="h-4 w-4" />
            </div>
          </div>
          <div>
            <div className="text-2xl font-bold text-gray-900 font-mono">
              {stats ? stats.approvals.pending : "-"}
            </div>
            <p className="text-xs text-gray-500 mt-1 flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-amber-500"></span>
              Awaiting human authorization
            </p>
          </div>
        </div>

        {/* Applied / Completed Actions */}
        <div className="bg-white border border-gray-200 p-5 rounded-2xl space-y-3 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-gray-600">Applied to GitHub</span>
            <div className="p-2 rounded-lg bg-emerald-50 text-emerald-600 border border-emerald-200">
              <CheckCircle2 className="h-4 w-4" />
            </div>
          </div>
          <div>
            <div className="text-2xl font-bold text-gray-900 font-mono">
              {stats ? stats.approvals.applied : "-"}
            </div>
            <p className="text-xs text-gray-500 mt-1 flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500"></span>
              Commits &amp; issues confirmed
            </p>
          </div>
        </div>

        {/* Total Job Queue */}
        <div className="bg-white border border-gray-200 p-5 rounded-2xl space-y-3 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-gray-600">Jobs Executed</span>
            <div className="p-2 rounded-lg bg-sky-50 text-[#088ADA] border border-sky-200">
              <Layers className="h-4 w-4" />
            </div>
          </div>
          <div>
            <div className="text-2xl font-bold text-gray-900 font-mono">
              {stats ? stats.jobs.total : "-"}
            </div>
            <p className="text-xs text-gray-500 mt-1 flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-emerald-500"></span>
              {stats ? `${stats.jobs.completed} successful` : "Background workers"}
            </p>
          </div>
        </div>

        {/* Conversations Tracked */}
        <div className="bg-white border border-gray-200 p-5 rounded-2xl space-y-3 shadow-sm">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-gray-600">Memory History</span>
            <div className="p-2 rounded-lg bg-sky-50 text-[#088ADA] border border-sky-200">
              <MessagesSquare className="h-4 w-4" />
            </div>
          </div>
          <div>
            <div className="text-2xl font-bold text-gray-900 font-mono">
              {stats ? stats.conversations : "-"}
            </div>
            <p className="text-xs text-gray-500 mt-1 flex items-center gap-1.5">
              <span className="h-1.5 w-1.5 rounded-full bg-[#088ADA]"></span>
              Stored conversation turns
            </p>
          </div>
        </div>
      </div>

      {/* Main Two-Column Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Pending Approvals Live Queue */}
        <div className={`${isMasterAdmin ? "lg:col-span-2" : "lg:col-span-3"} space-y-4`}>
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <ShieldAlert className="h-4 w-4 text-[#088ADA]" />
              <h2 className="text-sm font-semibold text-gray-600">Pending Write Approvals</h2>
            </div>
            <Link
              href="/approvals"
              className="text-xs text-[#088ADA] hover:text-[#0778bd] font-medium flex items-center gap-1"
            >
              <span>View All</span>
              <ArrowUpRight className="h-3.5 w-3.5" />
            </Link>
          </div>

          {pendingApprovals.length === 0 ? (
            <div className="bg-white border border-gray-200 rounded-2xl p-8 text-center space-y-2 shadow-sm">
              <div className="h-10 w-10 mx-auto rounded-full bg-emerald-50 border border-emerald-200 flex items-center justify-center text-emerald-600">
                <CheckCircle2 className="h-5 w-5" />
              </div>
              <h3 className="text-sm font-semibold text-gray-700">No Pending Approvals</h3>
              <p className="text-xs text-gray-500 max-w-sm mx-auto">
                All write proposals have been reviewed. When Claude generates a file or commit via Slack, it will appear here for one-click authorization.
              </p>
            </div>
          ) : (
            <div className="space-y-3">
              {pendingApprovals.map((appr) => {
                const path = appr.tool_arguments?.path || "repository";
                const branch = appr.tool_arguments?.branch || "main";
                const message = appr.tool_arguments?.message || "Commit changes";
                const isProcessing = actionLoading === appr.approval_id;

                return (
                  <div
                    key={appr.approval_id}
                    className="bg-white border border-gray-200 hover:border-gray-300 transition rounded-2xl p-5 space-y-4 shadow-sm"
                  >
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-bold px-2 py-0.5 rounded bg-sky-50 text-[#088ADA] border border-sky-200">
                            {appr.tool_name}
                          </span>
                          <span className="text-xs text-gray-500 font-mono">
                            ID: {appr.approval_id}
                          </span>
                        </div>
                        <h3 className="text-sm font-semibold text-gray-800 flex items-center gap-1.5">
                          <GitBranch className="h-3.5 w-3.5 text-[#088ADA]" />
                          <span>{path}</span>
                          <span className="text-xs text-gray-500 font-normal">({branch})</span>
                        </h3>
                        <p className="text-xs text-gray-500 italic">"{message}"</p>
                      </div>

                      <div className="flex items-center gap-2">
                        <button
                          disabled={isProcessing}
                          onClick={() => handleAction(appr.approval_id, "reject")}
                          className="px-3 py-1.5 rounded-lg bg-rose-50 hover:bg-rose-100 text-rose-600 border border-rose-200 text-xs font-semibold transition disabled:opacity-50"
                        >
                          Reject
                        </button>
                        <button
                          disabled={isProcessing}
                          onClick={() => handleAction(appr.approval_id, "approve")}
                          className="px-4 py-1.5 rounded-lg bg-[#088ADA] hover:bg-[#0778bd] text-white text-xs font-semibold shadow transition disabled:opacity-50 flex items-center gap-1.5"
                        >
                          {isProcessing ? (
                            <RefreshCw className="h-3 w-3 animate-spin" />
                          ) : (
                            <CheckCircle2 className="h-3.5 w-3.5" />
                          )}
                          <span>Approve &amp; Apply</span>
                        </button>
                      </div>
                    </div>

                    {appr.diff_preview && (
                      <div className="bg-gray-50 rounded-lg p-3 border border-gray-200 text-[11px] font-mono overflow-x-auto max-h-36 text-gray-600">
                        <pre>{appr.diff_preview}</pre>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Right 1 Col: Services & Health (JTS Master Admin Only) */}
        {isMasterAdmin && (
          <div className="space-y-4">
            <h2 className="text-sm font-semibold text-gray-700 flex items-center gap-2">
              <Activity className="h-4 w-4 text-emerald-600" />
              <span>Service Health &amp; Architecture</span>
            </h2>

            <div className="bg-white border border-gray-200 rounded-2xl p-5 space-y-4 shadow-sm">
              <div className="space-y-3 text-xs">
                <div className="flex items-center justify-between pb-3 border-b border-gray-200">
                  <span className="text-gray-600">Slack Webhook Router</span>
                  <span className="text-emerald-600 font-medium flex items-center gap-1.5">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500"></span>
                    Active (/api/slack/events)
                  </span>
                </div>
                <div className="flex items-center justify-between pb-3 border-b border-gray-200">
                  <span className="text-gray-600">Background Worker</span>
                  <span className="text-emerald-600 font-medium flex items-center gap-1.5">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500"></span>
                    Polling job_queue (1s)
                  </span>
                </div>
                <div className="flex items-center justify-between pb-3 border-b border-gray-200">
                  <span className="text-gray-600">GitHub MCP Server</span>
                  <span className="text-[#088ADA] font-medium flex items-center gap-1.5">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500"></span>
                    stdio (@modelcontextprotocol)
                  </span>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-gray-600">PostgreSQL Memory</span>
                  <span className="text-emerald-600 font-medium flex items-center gap-1.5">
                    <span className="h-1.5 w-1.5 rounded-full bg-emerald-500"></span>
                    Connected
                  </span>
                </div>
              </div>
            </div>

            {/* Quick Links Card */}
            <div className="bg-gray-50 border border-gray-200 rounded-xl p-4 space-y-2.5">
              <span className="text-xs font-medium text-gray-400">Direct Navigation</span>
              <div className="grid grid-cols-2 gap-2 text-xs">
                <Link
                  href="/logs"
                  className="p-2.5 rounded-lg bg-gray-100/60 hover:bg-gray-100 text-gray-600 flex items-center gap-2 transition"
                >
                  <Terminal className="h-3.5 w-3.5 text-[#088ADA]" />
                  <span>Live Logs</span>
                </Link>
                <Link
                  href="/database"
                  className="p-2.5 rounded-lg bg-gray-100/60 hover:bg-gray-100 text-gray-600 flex items-center gap-2 transition"
                >
                  <Layers className="h-3.5 w-3.5 text-emerald-600" />
                  <span>DB Explorer</span>
                </Link>
                <Link
                  href="/context"
                  className="p-2.5 rounded-lg bg-gray-100/60 hover:bg-gray-100 text-gray-600 flex items-center gap-2 transition col-span-2"
                >
                  <Sparkles className="h-3.5 w-3.5 text-[#088ADA]" />
                  <span>Claude Context Snapshots</span>
                </Link>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
