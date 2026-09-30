"use client";

import { useEffect, useState, useCallback, useMemo } from "react";
import {
  ShieldCheck,
  CheckCircle2,
  GitBranch,
  GitPullRequest,
  Search,
  RefreshCw,
  FileCode,
  Files,
  Hash,
  User,
  CircleDot,
  MessageSquare,
  Clock,
  ChevronLeft,
  ChevronRight,
  Loader2,
} from "lucide-react";
import { fetchApprovalsWithAccess, fetchApproval, submitApprovalAction } from "@/lib/api";
import { Approval, formatLocalDateTime } from "@/lib/types";
import {
  PageHeader,
  EmptyState,
  LoadingState,
  Alert,
  Card,
  StatusBadge,
  ConfirmDialog,
  btn,
} from "@/components/ui";

const STATUS_TABS: Array<{ value: string; label: string }> = [
  { value: "all", label: "All" },
  { value: "pending", label: "Waiting for review" },
  { value: "applied", label: "Approved" },
  { value: "failed", label: "Failed" },
  { value: "rejected", label: "Rejected" },
  { value: "expired", label: "Expired" },
];

const PAGE_SIZE = 10;

type Summary = {
  icon: React.ComponentType<{ className?: string }>;
  action: string;
  headline: string;
  details: string[];
};

/** Plain-language description of what an approval will do, per GitHub action. */
function describeApproval(a: Approval): Summary {
  const args = a.tool_arguments || {};
  const repo = args.owner && args.repo ? `${args.owner}/${args.repo}` : args.repo || "";
  const withRepo = (items: (string | false | undefined)[]) =>
    [repo && `Repository: ${repo}`, ...items].filter(Boolean) as string[];

  switch (a.tool_name) {
    case "create_or_update_file":
      return {
        icon: FileCode,
        action: "Change a file",
        headline: args.path || "(no file path)",
        details: withRepo([`Branch: ${args.branch || "main"}`, args.message && `Commit message: “${args.message}”`]),
      };
    case "push_files": {
      const files: any[] = args.files || [];
      return {
        icon: Files,
        action: "Change several files",
        headline: `${files.length} file${files.length === 1 ? "" : "s"}: ${files
          .slice(0, 3)
          .map((f) => f.path)
          .join(", ")}${files.length > 3 ? ", …" : ""}`,
        details: withRepo([`Branch: ${args.branch || "main"}`, args.message && `Commit message: “${args.message}”`]),
      };
    }
    case "create_branch":
      return {
        icon: GitBranch,
        action: "Create a branch",
        headline: args.branch || "(no branch name)",
        details: withRepo([`Starting from: ${args.from_branch || "the default branch"}`]),
      };
    case "create_pull_request":
      return {
        icon: GitPullRequest,
        action: "Open a pull request",
        headline: args.title || "(no title)",
        details: withRepo([`Merge “${args.head || "?"}” into “${args.base || "main"}”`]),
      };
    case "create_issue":
      return {
        icon: CircleDot,
        action: "Create an issue",
        headline: args.title || "(no title)",
        details: withRepo([args.labels?.length && `Labels: ${args.labels.join(", ")}`]),
      };
    case "update_issue":
      return {
        icon: CircleDot,
        action: "Update an issue",
        headline: `Issue #${args.issue_number ?? "?"}${args.title ? `: ${args.title}` : ""}`,
        details: withRepo([args.state && `Set state to: ${args.state}`]),
      };
    case "add_issue_comment":
      return {
        icon: MessageSquare,
        action: "Comment on an issue",
        headline: `Issue #${args.issue_number ?? "?"}`,
        details: withRepo([]),
      };
    default:
      return {
        icon: FileCode,
        action: a.tool_name.replace(/_/g, " "),
        headline: repo || "GitHub",
        details: [],
      };
  }
}

function channelLabel(a: Approval) {
  const ch = (a.channel_name || a.channel_id || "").trim();
  if (!ch) return "Unknown channel";
  return ch.replace(/^[#@]/, "");
}

function expiresIn(a: Approval): string | null {
  if (a.status !== "pending" || !a.expires_at) return null;
  const ms = new Date(a.expires_at).getTime() - Date.now();
  if (Number.isNaN(ms)) return null;
  if (ms <= 0) return "Expired";
  const hours = Math.floor(ms / 3_600_000);
  const minutes = Math.floor((ms % 3_600_000) / 60_000);
  return hours > 0 ? `Expires in ${hours}h ${minutes}m` : `Expires in ${minutes}m`;
}

function DiffView({ text, kind }: { text: string; kind?: Approval["diff_kind"] }) {
  if (kind === "text") {
    return (
      <pre className="mt-2 bg-gray-50 rounded-xl p-3 border border-gray-200 text-xs text-gray-700 whitespace-pre-wrap break-words max-h-72 overflow-auto leading-relaxed font-sans">
        {text}
      </pre>
    );
  }
  return (
    <div className="mt-2 bg-gray-50 rounded-xl p-3 border border-gray-200 text-xs font-mono overflow-x-auto max-h-96 leading-relaxed">
      {text.split("\n").map((line, idx) => {
        let cls = "text-gray-600";
        if (line.startsWith("+++") || line.startsWith("---")) cls = "text-gray-500 font-semibold";
        else if (line.startsWith("+")) cls = "text-emerald-800 bg-emerald-50";
        else if (line.startsWith("-")) cls = "text-rose-800 bg-rose-50";
        else if (line.startsWith("@@")) cls = "text-[#0778bd] bg-sky-50/60";
        return (
          <div key={idx} className={`${cls} px-1.5 rounded whitespace-pre`}>
            {line || " "}
          </div>
        );
      })}
    </div>
  );
}

function ChangesPanel({ appr }: { appr: Approval }) {
  const [open, setOpen] = useState(appr.status === "pending");
  const [detail, setDetail] = useState<Approval | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);
  const isFileChange = appr.tool_name === "create_or_update_file" || appr.tool_name === "push_files";

  useEffect(() => {
    if (!open || detail || loading || failed || !isFileChange) return;
    setLoading(true);
    fetchApproval(appr.approval_id)
      .then(setDetail)
      .catch(() => setFailed(true))
      .finally(() => setLoading(false));
  }, [open, detail, loading, failed, isFileChange, appr.approval_id]);

  const text = detail?.diff_preview || appr.diff_preview;
  const kind = detail?.diff_kind || appr.diff_kind;
  if (!text) return null;

  return (
    <details open={open} onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}>
      <summary className="text-xs font-medium text-[#088ADA] cursor-pointer select-none hover:underline">
        {isFileChange ? "Show what will change" : "Show details"}
      </summary>
      {isFileChange && loading && (
        <p className="mt-2 text-xs text-gray-500 flex items-center gap-1.5">
          <Loader2 className="h-3.5 w-3.5 animate-spin" />
          Comparing with the current file on GitHub...
        </p>
      )}
      {isFileChange && !loading && (failed || kind === "full") && (
        <p className="mt-2 text-xs text-amber-700">
          Couldn&apos;t compare with GitHub right now, so the whole new file is shown.
        </p>
      )}
      {isFileChange && !loading && detail && kind === "diff" && (
        <p className="mt-2 text-xs text-gray-500">
          Compared with the file on GitHub now. <span className="text-emerald-700">Green</span> lines are added,{" "}
          <span className="text-rose-700">red</span> lines are removed.
        </p>
      )}
      {!(isFileChange && loading) && <DiffView text={text} kind={kind} />}
    </details>
  );
}

export default function ApprovalsPage() {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [canApprove, setCanApprove] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [page, setPage] = useState(1);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ type: "success" | "error" | "warning"; message: string } | null>(null);
  const [confirming, setConfirming] = useState<{ appr: Approval; action: "approve" | "reject" } | null>(null);

  const loadApprovals = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const res = await fetchApprovalsWithAccess(statusFilter === "all" ? undefined : statusFilter);
      setApprovals(res.approvals);
      setCanApprove(res.canApprove);
    } catch (err: any) {
      setLoadError(err?.message || "We couldn't load approvals. Please refresh the page.");
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    loadApprovals();
  }, [loadApprovals]);

  useEffect(() => {
    setPage(1);
  }, [statusFilter, searchQuery]);

  async function runAction(appr: Approval, action: "approve" | "reject") {
    setActionLoading(appr.approval_id);
    setFeedback(null);
    try {
      const res = await submitApprovalAction(appr.approval_id, action);
      if (action === "approve" && !res.ok) {
        setFeedback({ type: "warning", message: res.message || "Approved, but GitHub returned an error. Nothing was changed." });
      } else {
        setFeedback({
          type: "success",
          message: res.message || (action === "approve" ? "Approved and applied on GitHub." : "Rejected."),
        });
      }
    } catch (err: any) {
      setFeedback({ type: "error", message: err?.message || `Could not ${action} this request. Please try again.` });
    } finally {
      setActionLoading(null);
      setConfirming(null);
      await loadApprovals();
    }
  }

  const filteredApprovals = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return approvals;
    return approvals.filter((a) => {
      const s = describeApproval(a);
      const haystack = [
        s.action,
        s.headline,
        ...s.details,
        a.approval_id,
        channelLabel(a),
        a.user_name,
        a.approved_by_name,
      ]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(q);
    });
  }, [approvals, searchQuery]);

  const totalPages = Math.max(1, Math.ceil(filteredApprovals.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const pageItems = filteredApprovals.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const pendingCount = approvals.filter((a) => a.status === "pending").length;

  const confirmSummary = confirming ? describeApproval(confirming.appr) : null;

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <PageHeader
        icon={ShieldCheck}
        title="Approvals"
        description="Before the bot changes anything on GitHub, an admin must approve it here or in Slack. Requests expire after 24 hours."
        badge={
          pendingCount > 0 ? (
            <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">
              {pendingCount} waiting
            </span>
          ) : undefined
        }
        actions={
          <button onClick={loadApprovals} className={btn.secondary}>
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            <span>Refresh</span>
          </button>
        }
      />

      {!loading && !canApprove && approvals.length > 0 && (
        <Alert type="info">You can follow your requests here. An admin approves or rejects them.</Alert>
      )}

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
            type="search"
            placeholder="Search file, issue, channel or person"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="w-full pl-9 pr-3 py-2 bg-white border border-gray-200 rounded-xl text-sm text-gray-700 placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:ring-2 focus:ring-[#088ADA]/20"
          />
        </div>
      </div>

      {feedback && (
        <Alert type={feedback.type} onClose={() => setFeedback(null)}>
          {feedback.message}
        </Alert>
      )}
      {loadError && <Alert type="error">{loadError}</Alert>}

      {/* List */}
      {loading && approvals.length === 0 ? (
        <LoadingState label="Loading approvals..." />
      ) : filteredApprovals.length === 0 ? (
        <EmptyState
          icon={ShieldCheck}
          title={
            searchQuery
              ? "No approvals match your search"
              : statusFilter === "pending"
              ? "Nothing is waiting for review"
              : "No approvals here yet"
          }
          description={
            searchQuery
              ? "Try a different search term."
              : "When someone asks the bot in Slack to change code or open an issue, the request appears here for approval."
          }
        />
      ) : (
        <div className="space-y-4">
          {pageItems.map((appr) => {
            const s = describeApproval(appr);
            const Icon = s.icon;
            const isPending = appr.status === "pending";
            const isProcessing = actionLoading === appr.approval_id;
            const expiry = expiresIn(appr);

            return (
              <Card key={appr.approval_id} className="p-5 space-y-4">
                <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-4">
                  <div className="space-y-2 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <StatusBadge status={appr.status} />
                      <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-sky-50 text-[#0778bd] border border-sky-200">
                        {s.action}
                      </span>
                      <span
                        className="text-[11px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-700 border border-gray-200 inline-flex items-center gap-1"
                        title={appr.channel_id ? `Channel ID: ${appr.channel_id}` : undefined}
                      >
                        <Hash className="h-3 w-3 text-gray-400" />
                        {channelLabel(appr)}
                      </span>
                      {expiry && (
                        <span className="text-[11px] text-amber-700 inline-flex items-center gap-1">
                          <Clock className="h-3 w-3" />
                          {expiry}
                        </span>
                      )}
                    </div>

                    <div className="flex items-center gap-2 text-sm font-semibold text-gray-800 min-w-0">
                      <Icon className="h-4 w-4 text-[#088ADA] shrink-0" />
                      <span className="truncate" title={s.headline}>
                        {s.headline}
                      </span>
                    </div>

                    {s.details.length > 0 && (
                      <ul className="text-xs text-gray-600 space-y-0.5">
                        {s.details.map((d) => (
                          <li key={d}>{d}</li>
                        ))}
                      </ul>
                    )}

                    <p className="text-xs text-gray-500 flex items-center gap-1.5 flex-wrap">
                      <User className="h-3.5 w-3.5" />
                      <span>
                        Requested by <strong className="text-gray-700">{appr.user_name || "Unknown"}</strong>
                      </span>
                      {appr.approved_by_name && (appr.status === "applied" || appr.status === "failed") && (
                        <>
                          <span className="text-gray-300">•</span>
                          <span>
                            Approved by <strong className="text-gray-700">{appr.approved_by_name}</strong>
                          </span>
                        </>
                      )}
                      {appr.approved_by_name && appr.status === "rejected" && (
                        <>
                          <span className="text-gray-300">•</span>
                          <span>
                            Rejected by <strong className="text-gray-700">{appr.approved_by_name}</strong>
                          </span>
                        </>
                      )}
                      <span className="text-gray-300">•</span>
                      <span>{formatLocalDateTime(appr.created_at)}</span>
                      <span className="text-gray-300">•</span>
                      <span className="font-mono text-gray-400">{appr.approval_id}</span>
                    </p>
                  </div>

                  {isPending && canApprove && (
                    <div className="flex items-center gap-2 shrink-0">
                      <button
                        disabled={isProcessing}
                        onClick={() => setConfirming({ appr, action: "reject" })}
                        className={btn.dangerSoft}
                      >
                        Reject
                      </button>
                      <button
                        disabled={isProcessing}
                        onClick={() => setConfirming({ appr, action: "approve" })}
                        className={btn.success}
                      >
                        {isProcessing ? <RefreshCw className="h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
                        <span>Approve &amp; apply</span>
                      </button>
                    </div>
                  )}
                </div>

                {!isPending && appr.execution_result && (
                  <div
                    className={`text-xs p-3 rounded-lg border ${
                      appr.status === "failed" ? "bg-rose-50 border-rose-200" : "bg-gray-50 border-gray-200"
                    }`}
                  >
                    <span className="block text-[11px] font-semibold text-gray-500 mb-0.5">
                      {appr.status === "failed" ? "GitHub error" : "Result from GitHub"}
                    </span>
                    <details>
                      <summary className="text-gray-700 font-mono break-words line-clamp-2 cursor-pointer">
                        {appr.execution_result}
                      </summary>
                      <pre className="mt-2 text-gray-700 font-mono whitespace-pre-wrap break-words max-h-60 overflow-auto">
                        {appr.execution_result}
                      </pre>
                    </details>
                  </div>
                )}

                <ChangesPanel appr={appr} />
              </Card>
            );
          })}

          {totalPages > 1 && (
            <div className="flex flex-col sm:flex-row items-center justify-between gap-3 text-xs text-gray-600">
              <span>
                Showing {(currentPage - 1) * PAGE_SIZE + 1}–{Math.min(currentPage * PAGE_SIZE, filteredApprovals.length)} of{" "}
                {filteredApprovals.length} requests
              </span>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => setPage(Math.max(1, currentPage - 1))}
                  disabled={currentPage === 1}
                  className={btn.secondary}
                  aria-label="Previous page"
                >
                  <ChevronLeft className="h-3.5 w-3.5" />
                  Previous
                </button>
                <span className="px-2">
                  Page {currentPage} of {totalPages}
                </span>
                <button
                  onClick={() => setPage(Math.min(totalPages, currentPage + 1))}
                  disabled={currentPage === totalPages}
                  className={btn.secondary}
                  aria-label="Next page"
                >
                  Next
                  <ChevronRight className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>
          )}
        </div>
      )}

      <ConfirmDialog
        open={Boolean(confirming)}
        busy={Boolean(actionLoading)}
        title={confirming?.action === "approve" ? "Approve and apply this change?" : "Reject this request?"}
        confirmLabel={confirming?.action === "approve" ? "Approve & apply" : "Reject"}
        confirmClass={confirming?.action === "approve" ? btn.success : btn.danger}
        onCancel={() => setConfirming(null)}
        onConfirm={() => confirming && runAction(confirming.appr, confirming.action)}
      >
        {confirming && confirmSummary && (
          <>
            <p>
              <strong>{confirmSummary.action}:</strong> {confirmSummary.headline}
            </p>
            {confirmSummary.details.map((d) => (
              <p key={d} className="text-xs text-gray-500">
                {d}
              </p>
            ))}
            <p className="text-xs">
              {confirming.action === "approve"
                ? "This runs on GitHub straight away and updates the request in Slack."
                : "Nothing will be changed on GitHub. The request in Slack will be marked as rejected."}
            </p>
          </>
        )}
      </ConfirmDialog>
    </div>
  );
}
