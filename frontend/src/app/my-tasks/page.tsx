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

  return (
    <div className="space-y-6 max-w-7xl mx-auto flex flex-col min-h-[calc(100vh-8rem)]">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-gray-200">
        <div className="flex items-center gap-2.5">
          <div className="h-9 w-9 rounded-xl bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA]">
            <ListTodo className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-gray-800 flex items-center gap-2">
              <span>My Tasks &amp; Activity Portal</span>
            </h1>
            <p className="text-xs text-gray-500 mt-0.5">
              View your personal task execution status, tool outputs, and action approval requests.
            </p>
          </div>
        </div>

        <button
          onClick={loadTasks}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-medium border border-gray-200 transition shadow-sm"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin text-[#088ADA]" : ""}`} />
          <span>Refresh</span>
        </button>
      </div>

      {/* Task Summary Badges */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <div className="p-4 rounded-2xl bg-white border border-gray-200 shadow-sm flex items-center gap-3">
          <div className="h-10 w-10 rounded-xl bg-gray-100 border border-gray-200 text-[#088ADA] flex items-center justify-center font-bold">
            <CheckCircle2 className="h-5 w-5" />
          </div>
          <div>
            <div className="text-xs font-semibold text-gray-600">Completed Actions</div>
            <div className="text-lg font-bold text-gray-900 font-mono">
              {approvals.filter((a) => a.status === "applied").length}
            </div>
          </div>
        </div>

        <div className="p-4 rounded-2xl bg-white border border-gray-200 shadow-sm flex items-center gap-3">
          <div className="h-10 w-10 rounded-xl bg-gray-100 border border-gray-200 text-[#088ADA] flex items-center justify-center font-bold">
            <Clock className="h-5 w-5" />
          </div>
          <div>
            <div className="text-xs font-semibold text-gray-600">Pending Approval</div>
            <div className="text-lg font-bold text-gray-900 font-mono">
              {approvals.filter((a) => a.status === "pending").length}
            </div>
          </div>
        </div>

        <div className="p-4 rounded-2xl bg-white border border-gray-200 shadow-sm flex items-center gap-3">
          <div className="h-10 w-10 rounded-xl bg-gray-100 border border-gray-200 text-[#088ADA] flex items-center justify-center font-bold">
            <ShieldAlert className="h-5 w-5" />
          </div>
          <div>
            <div className="text-xs font-semibold text-gray-600">Rejected / Expired</div>
            <div className="text-lg font-bold text-gray-900 font-mono">
              {approvals.filter((a) => a.status === "rejected" || a.status === "expired").length}
            </div>
          </div>
        </div>

        <div className="p-4 rounded-2xl bg-white border border-gray-200 shadow-sm flex items-center gap-3">
          <div className="h-10 w-10 rounded-xl bg-gray-100 border border-gray-200 text-[#088ADA] flex items-center justify-center font-bold">
            <AlertCircle className="h-5 w-5" />
          </div>
          <div>
            <div className="text-xs font-semibold text-gray-600">Total Activities</div>
            <div className="text-lg font-bold text-gray-900 font-mono">{approvals.length}</div>
          </div>
        </div>
      </div>

      {/* Task & Action History Table */}
      <div className="bg-white border border-gray-200 rounded-2xl overflow-hidden shadow-sm">
        <div className="p-4 border-b border-gray-200 bg-gray-50 flex items-center justify-between">
          <h2 className="text-xs font-bold text-gray-700 uppercase tracking-wider">My Activity Log ({approvals.length})</h2>
        </div>

        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs">
            <thead className="bg-gray-100/70 border-b border-gray-200 text-gray-500 uppercase font-semibold text-[10px]">
              <tr>
                <th className="p-3">Action / Tool</th>
                <th className="p-3">Channel</th>
                <th className="p-3">Arguments / Target</th>
                <th className="p-3">Status</th>
                <th className="p-3">Date</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 font-mono">
              {approvals.length === 0 ? (
                <tr>
                  <td colSpan={5} className="p-8 text-center text-gray-400 font-sans">
                    No task activity recorded yet.
                  </td>
                </tr>
              ) : (
                approvals.map((a) => (
                  <tr key={a.id} className="hover:bg-gray-50/60 transition">
                    <td className="p-3">
                      <div className="font-bold text-gray-800">{a.tool_name}</div>
                      <div className="text-[10px] text-gray-400 font-sans">ID: {a.approval_id}</div>
                    </td>
                    <td className="p-3 text-gray-700 font-sans">#{a.channel_id}</td>
                    <td className="p-3 text-gray-600 max-w-xs truncate">
                      {JSON.stringify(a.tool_arguments)}
                    </td>
                    <td className="p-3 font-sans">
                      {a.status === "applied" ? (
                        <span className="px-2 py-0.5 rounded bg-gray-100 text-gray-700 border border-gray-200 font-semibold text-[10px]">
                          Completed
                        </span>
                      ) : a.status === "pending" ? (
                        <span className="px-2 py-0.5 rounded bg-gray-100 text-gray-700 border border-gray-200 font-semibold text-[10px]">
                          Pending Approval
                        </span>
                      ) : (
                        <span className="px-2 py-0.5 rounded bg-gray-100 text-gray-600 border border-gray-200 font-semibold text-[10px]">
                          {a.status}
                        </span>
                      )}
                    </td>
                    <td className="p-3 text-gray-400 text-[10px] whitespace-nowrap">{formatLocalDateTime(a.created_at)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
