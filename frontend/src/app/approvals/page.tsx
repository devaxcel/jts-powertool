"use client";

import { useEffect, useState, useCallback } from "react";
import {
  ShieldCheck,
  CheckCircle2,
  GitBranch,
  Search,
  RefreshCw,
  FileCode,
  Hash,
  User,
} from "lucide-react";
import { fetchApprovals, submitApprovalAction, fetchFolder, fetchFolders } from "@/lib/api";
import { Approval, formatLocalDateTime } from "@/lib/types";
import { PageHeader, EmptyState, LoadingState, Alert, Card, StatusBadge, btn } from "@/components/ui";

const STATUS_TABS: Array<{ value: string; label: string }> = [
  { value: "all", label: "All" },
  { value: "pending", label: "Waiting for review" },
  { value: "applied", label: "Approved" },
  { value: "rejected", label: "Rejected" },
];

function formatUserDisplay(user?: string) {
  if (!user) return "Admin User";
  const clean = user.replace(/^<@|>$/g, "").trim();
  if (clean === "web-admin" || clean === "admin") return "Admin User";
  if (clean === "U0AQUL5KQMA" || clean === "U08SLP9LXUZ" || clean === "U0BSLP9LXUZ") return "Admin User";
  if (clean.startsWith("@")) return clean;
  return `@${clean}`;
}

function formatChannelDisplay(name?: string, id?: string) {
  const ch = (name || id || "general").trim();
  if (ch.startsWith("#") || ch.startsWith("@")) return ch;
  return `#${ch}`;
}

export default function ApprovalsPage() {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const loadApprovals = useCallback(async () => {
    setLoading(true);
    try {
      let activeRole = "jts_admin";
      let clientFolderId: number | null = null;
      if (typeof window !== "undefined") {
        const rawUser = sessionStorage.getItem("jts_user");
        const u = JSON.parse(rawUser || "{}");
        const savedSim = sessionStorage.getItem("jts_simulated_role");
        activeRole = savedSim || u.role || "jts_admin";
        clientFolderId = u.client_folder_id || 2;
      }

      let data = await fetchApprovals(statusFilter === "all" ? undefined : statusFilter);

      if (activeRole === "client_admin" || activeRole === "client_standard") {
        // CLIENT ADMIN (Filter HITLs strictly to channels inside client folder)
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

        data = data.filter((a) => {
          const cId = a.channel_id || (a.tool_arguments as any)?.channel_id;
          if (!cId) return true; // If no channel_id on approval, preserve it so it's not hidden
          if (channelIdSet.size === 0) return true;

          const raw = String(cId).toLowerCase();
          const clean = raw.replace(/^[@#]/, "");

          if (channelIdSet.has(raw) || channelIdSet.has(clean)) return true;

          return Array.from(channelIdSet).some((ch) => {
            const cleanCh = ch.replace(/^[@#]/, "");
            return cleanCh && (raw.includes(cleanCh) || clean.includes(cleanCh) || cleanCh.includes(clean));
          });
        });
      }

      setApprovals(data);
    } catch (err) {
      console.error("Error loading approvals:", err);
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    loadApprovals();
  }, [loadApprovals]);

  async function handleAction(id: string, action: "approve" | "reject") {
    setActionLoading(id);
    setActionError(null);
    try {
      let currentUsername = "web-admin";
      if (typeof window !== "undefined") {
        const rawUser = sessionStorage.getItem("jts_user");
        const u = JSON.parse(rawUser || "{}");
        currentUsername = u.username || u.display_name || "web-admin";
      }
      await submitApprovalAction(id, action, currentUsername);
      await loadApprovals();
    } catch (err: any) {
      console.error(err);
      setActionError(err?.message || `Could not ${action} this request. Please try again.`);
    } finally {
      setActionLoading(null);
    }
  }

  const filteredApprovals = approvals.filter((a) => {
    if (!searchQuery) return true;
    const q = searchQuery.toLowerCase();
    const path = (a.tool_arguments?.path || "").toLowerCase();
    const msg = (a.tool_arguments?.message || "").toLowerCase();
    const id = a.approval_id.toLowerCase();
    const channel = (a.channel_name || a.channel_id || "").toLowerCase();
    const user = (a.approved_by_name || a.approved_by || a.user_name || a.user_id || "").toLowerCase();
    return path.includes(q) || msg.includes(q) || id.includes(q) || channel.includes(q) || user.includes(q);
  });

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <PageHeader
        icon={ShieldCheck}
        title="Approvals"
        description="Before the bot changes anything on GitHub, a person must approve it here or in Slack."
        actions={
          <button onClick={loadApprovals} className={btn.secondary}>
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            <span>Refresh</span>
          </button>
        }
      />

      {/* Filters */}
      <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3">
        <div className="flex items-center gap-1 bg-white border border-gray-200 rounded-xl p-1 overflow-x-auto">
          {STATUS_TABS.map((tab) => (
            <button
              key={tab.value}
              onClick={() => setStatusFilter(tab.value)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap transition ${
                statusFilter === tab.value ? "bg-[#088ADA] text-white shadow-sm" : "text-gray-600 hover:text-gray-900 hover:bg-gray-100"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <div className="relative w-full sm:w-72">
          <Search className="h-4 w-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Search file, message, channel or person"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-3 py-2 bg-white border border-gray-200 rounded-xl text-sm text-gray-700 placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:ring-2 focus:ring-[#088ADA]/20"
          />
        </div>
      </div>

      {actionError && (
        <Alert type="error" onClose={() => setActionError(null)}>
          {actionError}
        </Alert>
      )}

      {/* List */}
      {loading ? (
        <LoadingState label="Loading approvals..." />
      ) : filteredApprovals.length === 0 ? (
        <EmptyState
          icon={ShieldCheck}
          title={searchQuery ? "No approvals match your search" : statusFilter === "pending" ? "Nothing is waiting for review" : "No approvals yet"}
          description={
            searchQuery
              ? "Try a different search term."
              : "When someone asks the bot in Slack to change code or open an issue, the request appears here for approval."
          }
        />
      ) : (
        <div className="space-y-4">
          {filteredApprovals.map((appr) => {
            const path = appr.tool_arguments?.path || "repository";
            const branch = appr.tool_arguments?.branch || "main";
            const message = appr.tool_arguments?.message || "Commit changes";
            const isPending = appr.status === "pending";
            const isProcessing = actionLoading === appr.approval_id;
            const actor = formatUserDisplay(appr.approved_by_name || appr.approved_by || appr.user_name || appr.user_id);

            return (
              <Card key={appr.approval_id} className="p-5 space-y-4">
                <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-4">
                  <div className="space-y-2 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <StatusBadge status={appr.status} />
                      <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-sky-50 text-[#0778bd] border border-sky-200">
                        {appr.tool_name.replace(/_/g, " ")}
                      </span>
                      <span
                        className="text-[11px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-700 border border-gray-200 inline-flex items-center gap-1"
                        title={appr.channel_id ? `Channel ID: ${appr.channel_id}` : undefined}
                      >
                        <Hash className="h-3 w-3 text-gray-400" />
                        {formatChannelDisplay(appr.channel_name, appr.channel_id).replace(/^#/, "")}
                      </span>
                    </div>

                    <div className="flex items-center gap-2 text-sm font-semibold text-gray-800 min-w-0">
                      <FileCode className="h-4 w-4 text-[#088ADA] shrink-0" />
                      <span className="truncate">{path}</span>
                      <span className="text-xs text-gray-500 font-normal inline-flex items-center gap-1 shrink-0">
                        <GitBranch className="h-3 w-3" />
                        {branch}
                      </span>
                    </div>

                    <p className="text-sm text-gray-600">&ldquo;{message}&rdquo;</p>

                    <p className="text-xs text-gray-500 flex items-center gap-1.5 flex-wrap">
                      <User className="h-3.5 w-3.5" />
                      {appr.status === "applied" && <>Approved by <strong className="text-gray-700">{actor}</strong></>}
                      {appr.status === "rejected" && <>Rejected by <strong className="text-gray-700">{actor}</strong></>}
                      {appr.status !== "applied" && appr.status !== "rejected" && (
                        <>Requested by <strong className="text-gray-700">{formatUserDisplay(appr.user_name || appr.user_id)}</strong></>
                      )}
                      <span className="text-gray-300">•</span>
                      <span>{formatLocalDateTime(appr.created_at)}</span>
                      <span className="text-gray-300">•</span>
                      <span className="font-mono text-gray-400">{appr.approval_id}</span>
                    </p>
                  </div>

                  {isPending && (
                    <div className="flex items-center gap-2 shrink-0">
                      <button disabled={isProcessing} onClick={() => handleAction(appr.approval_id, "reject")} className={btn.dangerSoft}>
                        Reject
                      </button>
                      <button disabled={isProcessing} onClick={() => handleAction(appr.approval_id, "approve")} className={btn.success}>
                        {isProcessing ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                        <span>Approve &amp; apply</span>
                      </button>
                    </div>
                  )}
                </div>

                {!isPending && appr.execution_result && (
                  <div className="text-xs bg-gray-50 p-3 rounded-lg border border-gray-200">
                    <span className="block text-[11px] font-semibold text-gray-500 mb-0.5">Result</span>
                    <p className="text-gray-700 font-mono break-words line-clamp-3">{appr.execution_result}</p>
                  </div>
                )}

                {appr.diff_preview && (
                  <details open={isPending}>
                    <summary className="text-xs font-medium text-[#088ADA] cursor-pointer select-none hover:underline">
                      Proposed changes
                    </summary>
                    <div className="mt-2 bg-gray-50 rounded-xl p-3 border border-gray-200 text-xs font-mono overflow-x-auto max-h-72 leading-relaxed">
                      {appr.diff_preview.split("\n").map((line, idx) => {
                        let cls = "text-gray-600";
                        if (line.startsWith("+")) cls = "text-emerald-800 bg-emerald-50";
                        else if (line.startsWith("-")) cls = "text-rose-800 bg-rose-50";
                        else if (line.startsWith("@")) cls = "text-[#0778bd]";
                        return (
                          <div key={idx} className={`${cls} px-1.5 rounded whitespace-pre`}>
                            {line || " "}
                          </div>
                        );
                      })}
                    </div>
                  </details>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
