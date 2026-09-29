"use client";

import { useEffect, useState, useCallback } from "react";
import {
  ShieldCheck,
  ShieldAlert,
  CheckCircle2,
  XCircle,
  Clock,
  GitBranch,
  Search,
  RefreshCw,
  FileCode,
  Hash,
  User,
} from "lucide-react";
import { fetchApprovals, submitApprovalAction, fetchFolder, fetchFolders } from "@/lib/api";
import { Approval, formatLocalDateTime } from "@/lib/types";

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
    try {
      let currentUsername = "web-admin";
      if (typeof window !== "undefined") {
        const rawUser = sessionStorage.getItem("jts_user");
        const u = JSON.parse(rawUser || "{}");
        currentUsername = u.username || u.display_name || "web-admin";
      }
      await submitApprovalAction(id, action, currentUsername);
      await loadApprovals();
    } catch (err) {
      console.error(err);
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
      {/* Header & Controls */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-bold text-gray-700 flex items-center gap-2">
            <ShieldCheck className="h-5 w-5 text-[#088ADA]" />
            <span>Human-in-the-Loop Approvals</span>
          </h1>
          <p className="text-xs text-gray-400 mt-0.5">
            Audit, inspect code diffs, and authorize Claude's GitHub write proposals.
          </p>
        </div>

        <button
          onClick={loadApprovals}
          className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-500 text-xs font-medium border border-gray-200 self-start sm:self-auto transition"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
          <span>Refresh</span>
        </button>
      </div>

      {/* Filter Tabs & Search */}
      <div className="flex flex-col sm:flex-row items-center justify-between gap-3 bg-gray-50 p-3 rounded-xl border border-gray-200">
        <div className="flex items-center gap-1.5 overflow-x-auto w-full sm:w-auto">
          {["all", "pending", "applied", "rejected"].map((tab) => (
            <button
              key={tab}
              onClick={() => setStatusFilter(tab)}
              className={`px-3 py-1.5 rounded-lg text-xs font-medium capitalize transition ${
                statusFilter === tab
                  ? "bg-[#088ADA] text-white shadow font-semibold"
                  : "text-gray-500 hover:text-gray-800 hover:bg-gray-200/60"
              }`}
            >
              {tab}
            </button>
          ))}
        </div>

        <div className="relative w-full sm:w-64">
          <Search className="h-3.5 w-3.5 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
          <input
            type="text"
            placeholder="Search by path, commit, channel, user..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-3 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs text-gray-600 placeholder-gray-400 focus:outline-none focus:border-[#088ADA]"
          />
        </div>
      </div>

      {/* Approvals List */}
      {loading ? (
        <div className="p-12 text-center text-gray-400 text-xs flex items-center justify-center gap-2">
          <RefreshCw className="h-4 w-4 animate-spin text-[#088ADA]" />
          <span>Loading approvals...</span>
        </div>
      ) : filteredApprovals.length === 0 ? (
        <div className="p-12 text-center bg-gray-50/40 border border-gray-200/80 rounded-xl space-y-2">
          <ShieldAlert className="h-8 w-8 mx-auto text-gray-300" />
          <h3 className="text-sm font-medium text-gray-500">No Approvals Found</h3>
          <p className="text-xs text-gray-400">
            {statusFilter !== "all"
              ? `No requests matching status '${statusFilter}'.`
              : "No approval proposals have been requested yet."}
          </p>
        </div>
      ) : (
        <div className="space-y-4">
          {filteredApprovals.map((appr) => {
            const path = appr.tool_arguments?.path || "repository";
            const branch = appr.tool_arguments?.branch || "main";
            const message = appr.tool_arguments?.message || "Commit changes";
            const isPending = appr.status === "pending";
            const isProcessing = actionLoading === appr.approval_id;

            return (
              <div
                key={appr.approval_id}
                className="bg-gray-50/90 border border-gray-200 rounded-xl p-6 space-y-4 shadow-sm"
              >
                {/* Header Row */}
                <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4 pb-4 border-b border-gray-200/80">
                  <div className="space-y-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-xs font-bold px-2 py-0.5 rounded bg-gray-100 text-[#088ADA] border border-gray-200">
                        {appr.tool_name}
                      </span>
                      <span className="text-xs font-mono text-gray-400">
                        ID: {appr.approval_id} • {formatLocalDateTime(appr.created_at)}
                      </span>

                      {/* Channel Badge */}
                      <span
                        className="text-[11px] font-medium px-2 py-0.5 rounded bg-blue-50 text-blue-700 border border-blue-200 flex items-center gap-1"
                        title={appr.channel_id ? `Channel ID: ${appr.channel_id}` : undefined}
                      >
                        <Hash className="h-3 w-3 text-blue-500" />
                        <span>{formatChannelDisplay(appr.channel_name, appr.channel_id)}</span>
                      </span>

                      {/* Status Badge */}
                      {appr.status === "pending" && (
                        <span className="text-[11px] font-semibold px-2 py-0.5 rounded bg-amber-50 text-amber-700 border border-amber-200 flex items-center gap-1">
                          <Clock className="h-3 w-3 text-amber-500" />
                          Pending Review
                        </span>
                      )}
                      {appr.status === "applied" && (
                        <span className="text-[11px] font-semibold px-2 py-0.5 rounded bg-emerald-50 text-emerald-600 border border-emerald-200 flex items-center gap-1">
                          <CheckCircle2 className="h-3 w-3" />
                          Applied to GitHub
                        </span>
                      )}
                      {appr.status === "rejected" && (
                        <span className="text-[11px] font-semibold px-2 py-0.5 rounded bg-rose-50 text-rose-500 border border-rose-500/20 flex items-center gap-1">
                          <XCircle className="h-3 w-3" />
                          Rejected
                        </span>
                      )}

                      {/* Actor Badge: Who Applied, Rejected, or Requested */}
                      {appr.status === "applied" && (
                        <span className="text-[11px] font-medium px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200 flex items-center gap-1">
                          <User className="h-3 w-3 text-emerald-600" />
                          <span>
                            Applied by{" "}
                            <strong className="font-semibold text-emerald-800">
                              {formatUserDisplay(appr.approved_by_name || appr.approved_by || appr.user_name || appr.user_id)}
                            </strong>
                          </span>
                        </span>
                      )}
                      {appr.status === "rejected" && (
                        <span className="text-[11px] font-medium px-2 py-0.5 rounded bg-rose-50 text-rose-700 border border-rose-200 flex items-center gap-1">
                          <User className="h-3 w-3 text-rose-600" />
                          <span>
                            Rejected by{" "}
                            <strong className="font-semibold text-rose-800">
                              {formatUserDisplay(appr.approved_by_name || appr.approved_by || appr.user_name || appr.user_id)}
                            </strong>
                          </span>
                        </span>
                      )}
                      {appr.status === "pending" && (
                        <span className="text-[11px] font-medium px-2 py-0.5 rounded bg-gray-100 text-gray-700 border border-gray-200 flex items-center gap-1">
                          <User className="h-3 w-3 text-gray-500" />
                          <span>
                            Requested by{" "}
                            <strong className="font-semibold text-gray-800">
                              {formatUserDisplay(appr.user_name || appr.user_id)}
                            </strong>
                          </span>
                        </span>
                      )}
                    </div>

                    <div className="flex items-center gap-3 text-sm font-semibold text-gray-700">
                      <span className="flex items-center gap-1 text-gray-500">
                        <FileCode className="h-4 w-4 text-[#088ADA]" />
                        {path}
                      </span>
                      <span className="text-xs text-gray-400 font-normal flex items-center gap-1">
                        <GitBranch className="h-3 w-3" />
                        {branch}
                      </span>
                    </div>

                    <p className="text-xs text-gray-500">
                      <span className="text-gray-400">Commit Message:</span> "{message}"
                    </p>
                  </div>

                  {/* Actions (if Pending) */}
                  {isPending && (
                    <div className="flex items-center gap-2 self-start lg:self-auto">
                      <button
                        disabled={isProcessing}
                        onClick={() => handleAction(appr.approval_id, "reject")}
                        className="px-3.5 py-1.5 rounded-lg bg-rose-50 hover:bg-rose-500/20 text-rose-600 border border-rose-500/20 text-xs font-semibold transition disabled:opacity-50"
                      >
                        Reject
                      </button>
                      <button
                        disabled={isProcessing}
                        onClick={() => handleAction(appr.approval_id, "approve")}
                        className="px-4 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-gray-800 text-xs font-semibold shadow transition disabled:opacity-50 flex items-center gap-1.5"
                      >
                        {isProcessing ? (
                          <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <CheckCircle2 className="h-3.5 w-3.5" />
                        )}
                        <span>Approve & Commit</span>
                      </button>
                    </div>
                  )}

                  {/* Execution result (if applied/failed) */}
                  {!isPending && appr.execution_result && (
                    <div className="text-xs text-gray-400 max-w-md bg-gray-50/60 p-2.5 rounded-lg border border-gray-200/80 font-mono">
                      <span className="text-gray-400 block text-[10px] uppercase font-bold">Result:</span>
                      <p className="text-gray-500 truncate">{appr.execution_result}</p>
                    </div>
                  )}
                </div>

                {/* Diff Viewer Section */}
                {appr.diff_preview && (
                  <div className="space-y-1.5">
                    <span className="text-xs font-medium text-gray-400 flex items-center justify-between">
                      <span>Proposed Code Changes</span>
                      <span className="text-[10px] text-gray-400 font-mono">Unified Diff</span>
                    </span>
                    <div className="bg-gray-50 rounded-xl p-4 border border-gray-200 text-xs font-mono overflow-x-auto max-h-72 leading-relaxed">
                      {appr.diff_preview.split("\n").map((line, idx) => {
                        let color = "text-gray-500";
                        let bg = "bg-transparent";
                        if (line.startsWith("+")) {
                          color = "text-emerald-600";
                          bg = "bg-emerald-950/25";
                        } else if (line.startsWith("-")) {
                          color = "text-rose-600";
                          bg = "bg-rose-950/25";
                        } else if (line.startsWith("@")) {
                          color = "text-[#088ADA]";
                        }
                        return (
                          <div key={idx} className={`${color} ${bg} px-1.5 py-0.5 rounded`}>
                            {line}
                          </div>
                        );
                      })}
                    </div>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
