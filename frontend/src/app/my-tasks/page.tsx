"use client";

import { useEffect, useState, useCallback } from "react";
import {
  ListTodo,
  CheckCircle2,
  Clock,
  AlertCircle,
  ShieldAlert,
  RefreshCw,
} from "lucide-react";
import { fetchApprovals } from "@/lib/api";
import { DataTable } from "@/components/DataTable";
import { Approval, formatLocalDateTime } from "@/lib/types";
import { PageHeader, StatCard, EmptyState, LoadingState, Card, StatusBadge, btn } from "@/components/ui";

export default function MyTasksPage() {
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [loading, setLoading] = useState(true);

  const loadTasks = useCallback(async () => {
    setLoading(true);
    try {
      const list = await fetchApprovals();
      setApprovals(list);
    } catch (e) {
      console.error("Failed to load user tasks:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadTasks();
  }, [loadTasks]);

  const describeTarget = (a: Approval) => {
    const args = a.tool_arguments || {};
    return args.path || args.title || (args.issue_number ? `Issue #${args.issue_number}` : "") || args.branch || "Repository";
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <PageHeader
        icon={ListTodo}
        title="My activity"
        description="Changes you asked the bot to make, and whether they were approved."
        actions={
          <button onClick={loadTasks} className={btn.secondary}>
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            <span>Refresh</span>
          </button>
        }
      />

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatCard label="Completed" value={approvals.filter((a) => a.status === "applied").length} icon={CheckCircle2} tone="green" />
        <StatCard label="Waiting for review" value={approvals.filter((a) => a.status === "pending").length} icon={Clock} tone="amber" />
        <StatCard
          label="Rejected or expired"
          value={approvals.filter((a) => a.status === "rejected" || a.status === "expired").length}
          icon={ShieldAlert}
          tone="rose"
        />
        <StatCard label="Total requests" value={approvals.length} icon={AlertCircle} tone="gray" />
      </div>

      {loading && approvals.length === 0 ? (
        <LoadingState label="Loading your activity..." />
      ) : approvals.length === 0 ? (
        <EmptyState
          icon={ListTodo}
          title="No activity yet"
          description="When you ask the bot in Slack to change code or create an issue, the request will show up here."
        />
      ) : (
        <Card className="overflow-hidden">
          <DataTable
            rows={approvals}
            rowKey={(a) => a.id}
            itemLabel="requests"
            searchPlaceholder="Search request, channel or status"
            initialSort={{ key: "created_at", dir: "desc" }}
            columns={[
              {
                key: "request",
                header: "Request",
                sortValue: (a) => a.tool_name,
                searchValue: (a) => `${a.tool_name.replace(/_/g, " ")} ${describeTarget(a)}`,
                render: (a) => (
                  <>
                    <div className="font-medium text-sm text-gray-800 capitalize">{a.tool_name.replace(/_/g, " ")}</div>
                    <div className="text-xs text-gray-500 truncate max-w-xs" title={JSON.stringify(a.tool_arguments)}>
                      {describeTarget(a)}
                    </div>
                  </>
                ),
              },
              {
                key: "channel",
                header: "Channel",
                sortValue: (a) => a.channel_name || a.channel_id || "",
                className: "text-sm text-gray-700",
                render: (a) => (a.channel_name ? a.channel_name : <span className="font-mono text-xs">{a.channel_id}</span>),
              },
              {
                key: "status",
                header: "Status",
                render: (a) => <StatusBadge status={a.status} />,
              },
              {
                key: "created_at",
                header: "Date",
                sortValue: (a) => (a.created_at ? new Date(a.created_at).getTime() : null),
                searchValue: () => "",
                className: "text-xs text-gray-500 whitespace-nowrap",
                render: (a) => formatLocalDateTime(a.created_at),
              },
            ]}
          />
        </Card>
      )}
    </div>
  );
}
