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
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-gray-50 border-b border-gray-200 text-gray-500 text-xs">
                <tr>
                  <th className="px-4 py-3 font-medium">Request</th>
                  <th className="px-4 py-3 font-medium">Channel</th>
                  <th className="px-4 py-3 font-medium">Status</th>
                  <th className="px-4 py-3 font-medium">Date</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {approvals.map((a) => (
                  <tr key={a.id} className="hover:bg-gray-50 transition">
                    <td className="px-4 py-3">
                      <div className="font-medium text-gray-800 capitalize">{a.tool_name.replace(/_/g, " ")}</div>
                      <div className="text-xs text-gray-500 truncate max-w-xs" title={JSON.stringify(a.tool_arguments)}>
                        {describeTarget(a)}
                      </div>
                    </td>
                    <td className="px-4 py-3 text-gray-700">
                      {a.channel_name ? a.channel_name : <span className="font-mono text-xs">{a.channel_id}</span>}
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge status={a.status} />
                    </td>
                    <td className="px-4 py-3 text-xs text-gray-500 whitespace-nowrap">{formatLocalDateTime(a.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
