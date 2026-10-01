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
  ArrowRight,
  GitBranch,
  RefreshCw,
  Activity,
  DollarSign,
  Database,
  LayoutDashboard,
} from "lucide-react";
import { PageHeader, StatCard, EmptyState, Alert, Card, ConfirmDialog, btn } from "@/components/ui";
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
  const [actionError, setActionError] = useState<string | null>(null);
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
        clientFolderId = u.client_folder_id || -1; // -1 = not linked to a client (matches nothing)
      }

      if (activeRole === "client_admin" || activeRole === "client_standard") {
        // CLIENT ADMIN DASHBOARD (Strictly scoped to channels inside client folder)
        const targetFolderId = clientFolderId || -1;
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

  const [confirming, setConfirming] = useState<{ id: string; label: string; action: "approve" | "reject" } | null>(null);
  const [actionNotice, setActionNotice] = useState<string | null>(null);

  async function handleAction(id: string, action: "approve" | "reject") {
    setActionLoading(id);
    setActionError(null);
    try {
      let currentUsername = "Admin User";
      if (typeof window !== "undefined") {
        const rawUser = sessionStorage.getItem("jts_user");
        const u = JSON.parse(rawUser || "{}");
        currentUsername = u.username || u.display_name || "Admin User";
      }
      const res = await submitApprovalAction(id, action, currentUsername);
      if (action === "approve" && !res.ok) {
        setActionError(res.message || "Approved, but GitHub returned an error. Nothing was changed.");
      } else {
        setActionNotice(res.message || (action === "approve" ? "Approved and applied on GitHub." : "Rejected."));
      }
      await loadData();
    } catch (err: any) {
      console.error("Error submitting approval action:", err);
      setActionError(err?.message || `Could not ${action} this request. Please try again.`);
    } finally {
      setActionLoading(null);
      setConfirming(null);
    }
  }

  const fmtNum = (n?: number) => (typeof n === "number" ? n.toLocaleString() : "-");

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <PageHeader
        icon={LayoutDashboard}
        title={isMasterAdmin ? "Dashboard" : "Your dashboard"}
        description={
          isMasterAdmin
            ? "A quick look at what the Slack assistant is doing across all clients."
            : "A quick look at your team's assistant activity, approvals and costs."
        }
        actions={
          <>
            <button
              onClick={() => {
                setLoading(true);
                loadData();
              }}
              className={btn.secondary}
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              <span>Refresh</span>
            </button>
            <Link href="/approvals" className={btn.primary}>
              <ShieldAlert className="h-3.5 w-3.5" />
              <span>Review approvals</span>
            </Link>
          </>
        }
      />

      {/* Key numbers */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
        <StatCard
          label="AI usage cost"
          value={`$${usage ? usage.total_cost_usd.toFixed(4) : "0.0000"}`}
          hint={usage ? `${usage.total_tokens.toLocaleString()} tokens used` : "Calculated for every AI reply"}
          icon={DollarSign}
          tone="green"
        />
        <StatCard
          label="Waiting for review"
          value={stats ? fmtNum(stats.approvals.pending) : "-"}
          hint="Changes the bot wants to make"
          icon={Clock}
          tone="amber"
        />
        <StatCard
          label="Approved changes"
          value={stats ? fmtNum(stats.approvals.applied) : "-"}
          hint="Applied to GitHub"
          icon={CheckCircle2}
          tone="green"
        />
        <StatCard
          label="Requests handled"
          value={stats ? fmtNum(stats.jobs.total) : "-"}
          hint={stats ? `${stats.jobs.completed.toLocaleString()} completed successfully` : "Slack messages processed"}
          icon={Layers}
          tone="blue"
        />
        <StatCard
          label="Messages stored"
          value={stats ? fmtNum(stats.conversations) : "-"}
          hint="Conversation history the bot remembers"
          icon={MessagesSquare}
          tone="blue"
        />
      </div>

      {actionNotice && (
        <Alert type="success" onClose={() => setActionNotice(null)}>
          {actionNotice}
        </Alert>
      )}

      {actionError && (
        <Alert type="error" onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Pending approvals */}
        <div className={`${isMasterAdmin ? "lg:col-span-2" : "lg:col-span-3"} space-y-3`}>
          <div className="flex items-center justify-between">
            <div>
              <h2 className="text-sm font-semibold text-gray-800">Waiting for your review</h2>
              <p className="text-xs text-gray-500">The bot needs a person to approve these GitHub changes.</p>
            </div>
            <Link href="/approvals" className="text-xs text-[#088ADA] hover:text-[#0778bd] font-medium flex items-center gap-1">
              View all
              <ArrowRight className="h-3.5 w-3.5" />
            </Link>
          </div>

          {pendingApprovals.length === 0 ? (
            <EmptyState
              icon={CheckCircle2}
              title="You're all caught up"
              description="Nothing is waiting for review. When the bot proposes a GitHub change from Slack, it will show up here."
            />
          ) : (
            <div className="space-y-3">
              {pendingApprovals.map((appr) => {
                const path = appr.tool_arguments?.path || "repository";
                const branch = appr.tool_arguments?.branch || "main";
                const message = appr.tool_arguments?.message || "Commit changes";
                const isProcessing = actionLoading === appr.approval_id;

                return (
                  <Card key={appr.approval_id} className="p-5 space-y-4 hover:border-gray-300 transition">
                    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                      <div className="space-y-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-sky-50 text-[#0778bd] border border-sky-200">
                            {appr.tool_name.replace(/_/g, " ")}
                          </span>
                          <span className="text-[11px] text-gray-400 font-mono">{appr.approval_id}</span>
                        </div>
                        <h3 className="text-sm font-semibold text-gray-800 flex items-center gap-1.5">
                          <GitBranch className="h-3.5 w-3.5 text-[#088ADA] shrink-0" />
                          <span className="truncate">{path}</span>
                          <span className="text-xs text-gray-500 font-normal">on {branch}</span>
                        </h3>
                        <p className="text-xs text-gray-500">&ldquo;{message}&rdquo;</p>
                      </div>

                      <div className="flex items-center gap-2 shrink-0">
                        <button
                          disabled={isProcessing}
                          onClick={() => setConfirming({ id: appr.approval_id, label: `${appr.tool_name.replace(/_/g, " ")}: ${path}`, action: "reject" })}
                          className={btn.dangerSoft}
                        >
                          Reject
                        </button>
                        <button
                          disabled={isProcessing}
                          onClick={() => setConfirming({ id: appr.approval_id, label: `${appr.tool_name.replace(/_/g, " ")}: ${path}`, action: "approve" })}
                          className={btn.success}
                        >
                          {isProcessing ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                          <span>Approve</span>
                        </button>
                      </div>
                    </div>

                    {appr.diff_preview && (
                      <details className="group">
                        <summary className="text-xs text-[#088ADA] cursor-pointer select-none hover:underline">Show proposed changes</summary>
                        <pre className="mt-2 bg-gray-50 rounded-lg p-3 border border-gray-200 text-[11px] overflow-x-auto max-h-48 text-gray-700">
                          {appr.diff_preview}
                        </pre>
                      </details>
                    )}
                  </Card>
                );
              })}
            </div>
          )}
        </div>

        {/* Shortcuts (JTS Admin) */}
        {isMasterAdmin && (
          <div className="space-y-3">
            <div>
              <h2 className="text-sm font-semibold text-gray-800">Shortcuts</h2>
              <p className="text-xs text-gray-500">Jump to monitoring tools.</p>
            </div>
            <Card className="p-2">
              {[
                { href: "/logs", icon: Activity, label: "Activity log", desc: "Live events from Slack and the AI" },
                { href: "/context", icon: Sparkles, label: "Conversation inspector", desc: "See exactly what was sent to the AI" },
                { href: "/billing", icon: DollarSign, label: "Usage & billing", desc: "Costs per client, channel and user" },
                { href: "/database", icon: Database, label: "Database", desc: "Browse stored records" },
              ].map((l) => (
                <Link key={l.href} href={l.href} className="flex items-center gap-3 p-3 rounded-xl hover:bg-gray-50 transition">
                  <div className="h-9 w-9 rounded-lg bg-[#088ADA]/10 text-[#088ADA] flex items-center justify-center shrink-0">
                    <l.icon className="h-4 w-4" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-gray-800">{l.label}</div>
                    <div className="text-xs text-gray-500 truncate">{l.desc}</div>
                  </div>
                  <ArrowRight className="h-4 w-4 text-gray-300" />
                </Link>
              ))}
            </Card>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={Boolean(confirming)}
        busy={Boolean(actionLoading)}
        title={confirming?.action === "approve" ? "Approve and apply this change?" : "Reject this request?"}
        confirmLabel={confirming?.action === "approve" ? "Approve & apply" : "Reject"}
        confirmClass={confirming?.action === "approve" ? btn.success : btn.danger}
        onCancel={() => setConfirming(null)}
        onConfirm={() => confirming && handleAction(confirming.id, confirming.action)}
      >
        {confirming && (
          <>
            <p>{confirming.label}</p>
            <p className="text-xs">
              {confirming.action === "approve"
                ? "This runs on GitHub straight away. Open Approvals to see exactly what will change first."
                : "Nothing will be changed on GitHub."}
            </p>
          </>
        )}
      </ConfirmDialog>
    </div>
  );
}
