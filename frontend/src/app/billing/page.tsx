"use client";

import { useEffect, useState, useMemo, useCallback } from "react";
import {
  Receipt,
  DollarSign,
  Zap,
  Users,
  Layers,
  Search,
  RefreshCw,
  Cpu,
  FileText,
  User,
  Trash2,
  Building2,
  CalendarRange,
  Download,
  Calculator,
  BarChart3,
} from "lucide-react";
import { UsageSummary, ApiUsageLog, formatLocalDateTime } from "@/lib/types";
import { ClientApiKeyCard } from "@/components/ClientApiKeyCard";
import { InvoicesPanel } from "@/components/InvoicesPanel";
import { PageHeader, Alert, ConfirmDialog, Tabs, btn } from "@/components/ui";
import { DataTable, Column } from "@/components/DataTable";
import {
  DateRange,
  fetchBillingSummary,
  fetchUsageLogsInRange,
  downloadUsageCsv,
  recalculateUsageCosts,
  clearBillingData,
  deleteChannelBilling,
  deleteUserBilling,
  deleteChannelUserBilling,
  deleteSingleLog,
  getAuthoritativeWorkspace,
  AUTHORITATIVE_CHANNEL_NAMES,
} from "@/lib/api";
import { PERIOD_OPTIONS, PeriodPreset, describeRange, formatUsd, presetToRange } from "@/lib/periods";

/** Most recent replies loaded for the "Every reply" table (totals always cover the whole period). */
const LOG_LIMIT = 2000;

type PendingAction = {
  title: string;
  body: string;
  confirmLabel: string;
  danger: boolean;
  run: () => Promise<string>;
};

type WorkspaceRow = { workspace_id?: string | null; workspace_name?: string | null };
type TokenRow = { input_tokens: number; output_tokens: number; total_tokens: number };

function workspaceColumns<T extends WorkspaceRow>(show: boolean): Column<T>[] {
  if (!show) return [];
  return [
    {
      key: "workspace_id",
      header: "Workspace ID",
      render: (r) => (
        <span className="px-2 py-0.5 rounded bg-blue-50 text-blue-800 font-mono font-bold text-[10px] border border-blue-200">
          {r.workspace_id || "—"}
        </span>
      ),
    },
    {
      key: "workspace_name",
      header: "Slack workspace",
      className: "font-semibold text-gray-900",
      render: (r) => r.workspace_name || "—",
    },
  ];
}

function tokenColumns<T extends TokenRow>(cost: (r: T) => number): Column<T>[] {
  return [
    {
      key: "input_tokens",
      header: "Tokens read",
      align: "right",
      className: "font-mono text-gray-600",
      searchValue: () => "",
      render: (r) => r.input_tokens.toLocaleString(),
    },
    {
      key: "output_tokens",
      header: "Tokens written",
      align: "right",
      className: "font-mono text-gray-600",
      searchValue: () => "",
      render: (r) => r.output_tokens.toLocaleString(),
    },
    {
      key: "total_tokens",
      header: "Total tokens",
      align: "right",
      className: "font-mono font-bold text-gray-800",
      searchValue: () => "",
      render: (r) => r.total_tokens.toLocaleString(),
    },
    {
      key: "cost",
      header: "Cost (USD)",
      align: "right",
      className: "font-mono font-bold text-emerald-600",
      sortValue: cost,
      searchValue: () => "",
      render: (r) => `$${cost(r).toFixed(6)}`,
    },
  ];
}

function deleteColumn<T>(show: boolean, render: (r: T) => React.ReactNode): Column<T>[] {
  if (!show) return [];
  return [{ key: "actions", header: "Actions", sortable: false, searchValue: () => "", align: "right", render }];
}

const deleteBtnClass =
  "px-2.5 py-1 bg-red-50 hover:bg-red-100 text-red-700 rounded-lg text-xs font-semibold transition border border-red-200 inline-flex items-center gap-1 disabled:opacity-50";

export default function BillingPage() {
  const [billingData, setBillingData] = useState<UsageSummary | null>(null);
  const [usageLogs, setUsageLogs] = useState<ApiUsageLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedWorkspace, setSelectedWorkspace] = useState<string>("ALL");
  const [activeTab, setActiveTab] = useState<"channel" | "user" | "channel_user" | "logs">("channel");
  const [view, setView] = useState<"usage" | "invoices">("usage");
  const [preset, setPreset] = useState<PeriodPreset>("this_month");
  const [customRange, setCustomRange] = useState<DateRange>(() => presetToRange("this_month"));
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [exporting, setExporting] = useState(false);
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [actionBusy, setActionBusy] = useState(false);

  // RBAC state (display only; the server enforces access)
  const [userRole, setUserRole] = useState<string>("jts_admin");
  const [myFolderId, setMyFolderId] = useState<number | null>(null);

  useEffect(() => {
    if (typeof window !== "undefined") {
      try {
        const rawUser = sessionStorage.getItem("jts_user");
        const u = JSON.parse(rawUser || "{}");
        const savedSim = sessionStorage.getItem("jts_simulated_role");
        const r = savedSim || u.role || "jts_admin";
        setUserRole(r);
        if (u.client_folder_id) setMyFolderId(Number(u.client_folder_id));
        if (new URLSearchParams(window.location.search).get("view") === "invoices") setView("invoices");
      } catch {}
    }
  }, []);

  const isMasterAdmin = !userRole || userRole === "admin" || userRole === "jts_admin";
  const canSeeInvoices = isMasterAdmin || userRole === "client_admin";

  const range = useMemo(() => presetToRange(preset, customRange), [preset, customRange]);
  const rangeLabel = describeRange(range);
  const rangeValid = !(range.start && range.end && range.end < range.start);

  function formatChannelName(name: string) {
    if (!name) return "";
    if (name.startsWith("@") || name.startsWith("#")) {
      return name;
    }
    return `#${name}`;
  }

  function canonicalChannelId(cid: string, ws?: string, channelName?: string): string {
    if (!cid) return "";
    const clean = cid.trim().toUpperCase();
    const wsLower = (ws || "").toLowerCase();
    const nameLower = (channelName || "").toLowerCase();

    // JTS Team workspace channel #jts_powertool MUST be C0C28B8V2PK
    if (wsLower.includes("jts") || ws === "T02HKMBE09K") {
      if (clean === "C08MV3EM9PY" || clean === "C0BMV3EM9PY" || clean === "C0C28B8V2PK" || nameLower === "#jts_powertool" || nameLower === "jts_powertool") {
        return "C0C28B8V2PK";
      }
    }

    // Axcel World workspace channel #jts_powertool MUST be C08MV3EM9PY
    if (wsLower.includes("axcel") || ws === "T5ZMF56H5") {
      if (clean === "C0BMV3EM9PY" || clean === "C08MV3EM9PY" || nameLower === "#jts_powertool" || nameLower === "jts_powertool") {
        return "C08MV3EM9PY";
      }
    }

    if (clean === "C0BMV3EM9PY") return "C08MV3EM9PY";
    if (clean === "C0BV6S5UJ0P") return "C08V6S5UJ0P";
    if (clean === "D0BSLP9LXUZ") return "D08SLP9LXUZ";
    return cid.trim();
  }

  /** Merges rows that refer to the same channel/person under different legacy IDs (display only). */
  function consolidate(sum: UsageSummary, logList: ApiUsageLog[]): { sum: UsageSummary; logs: ApiUsageLog[] } {
    const merge = (existing: any, item: any) => {
      existing.calls = (existing.calls || 0) + (item.calls || 0);
      existing.input_tokens = (existing.input_tokens || 0) + (item.input_tokens || 0);
      existing.output_tokens = (existing.output_tokens || 0) + (item.output_tokens || 0);
      existing.total_tokens = (existing.total_tokens || 0) + (item.total_tokens || 0);
      existing.total_cost_usd = Number(((existing.total_cost_usd || 0) + (item.total_cost_usd || 0)).toFixed(6));
    };

    const chanMap = new Map<string, any>();
    (sum.by_channel || []).forEach((item: any) => {
      const cid = canonicalChannelId(item.channel_id, item.workspace_id || item.workspace_name || "", item.channel_name);
      const authWs = getAuthoritativeWorkspace(cid, item.workspace_id, item.workspace_name);
      const key = cid.toUpperCase();
      if (!key) return;
      const row = {
        ...item,
        channel_id: cid,
        workspace_id: authWs.id,
        workspace_name: authWs.name,
        channel_name: AUTHORITATIVE_CHANNEL_NAMES[key] || item.channel_name,
      };
      if (!chanMap.has(key)) chanMap.set(key, row);
      else merge(chanMap.get(key), row);
    });
    sum.by_channel = Array.from(chanMap.values()).sort((a, b) => b.total_cost_usd - a.total_cost_usd);

    const userMap = new Map<string, any>();
    (sum.by_user || []).forEach((item: any) => {
      const key = ((item.user_id || "").trim() || (item.user_name || "").trim()).toUpperCase();
      if (!key) return;
      if (!userMap.has(key)) userMap.set(key, { ...item });
      else merge(userMap.get(key), item);
    });
    sum.by_user = Array.from(userMap.values()).sort((a, b) => b.total_cost_usd - a.total_cost_usd);

    const cuMap = new Map<string, any>();
    (sum.by_channel_user || []).forEach((item: any) => {
      const cid = canonicalChannelId(item.channel_id, item.workspace_id || item.workspace_name || "", item.channel_name);
      const authWs = getAuthoritativeWorkspace(cid, item.workspace_id, item.workspace_name);
      const cKey = cid.toUpperCase();
      const key = `${cKey}::${((item.user_id || "").trim() || (item.user_name || "").trim()).toUpperCase()}`;
      const row = {
        ...item,
        channel_id: cid,
        workspace_id: authWs.id,
        workspace_name: authWs.name,
        channel_name: AUTHORITATIVE_CHANNEL_NAMES[cKey] || item.channel_name,
      };
      if (!cuMap.has(key)) cuMap.set(key, row);
      else merge(cuMap.get(key), row);
    });
    sum.by_channel_user = Array.from(cuMap.values()).sort((a, b) => b.total_cost_usd - a.total_cost_usd);

    if (sum.workspaces) {
      const wsMap = new Map<string, any>();
      sum.workspaces.forEach((w: any) => {
        if (w.workspace_id && w.workspace_id !== "UNKNOWN" && !w.workspace_id.startsWith("wrkspc_")) wsMap.set(w.workspace_id, w);
      });
      sum.workspaces = Array.from(wsMap.values());
    }

    const logs = Array.from(
      new Map(
        (logList || []).map((item: any) => {
          const cid = canonicalChannelId(item.channel_id, item.workspace_id || item.workspace_name || "", item.channel_name);
          const authWs = getAuthoritativeWorkspace(cid, item.workspace_id, item.workspace_name);
          return [
            item.id,
            {
              ...item,
              channel_id: cid,
              channel_name: AUTHORITATIVE_CHANNEL_NAMES[cid.toUpperCase()] || item.channel_name,
              workspace_id: authWs.id,
              workspace_name: authWs.name,
            },
          ];
        })
      ).values()
    ) as ApiUsageLog[];

    return { sum, logs };
  }

  const loadBillingData = useCallback(async () => {
    if (!rangeValid) return;
    setLoading(true);
    try {
      const [sum, logList] = await Promise.all([fetchBillingSummary(range), fetchUsageLogsInRange(LOG_LIMIT, range)]);
      const merged = consolidate(sum, logList);
      setBillingData(merged.sum);
      setUsageLogs(merged.logs);
      setLoadError(null);
    } catch (err: any) {
      setLoadError(err?.message || "We couldn't load billing data. Please refresh the page.");
    } finally {
      setLoading(false);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [range.start, range.end, rangeValid]);

  useEffect(() => {
    if (view !== "usage") return;
    loadBillingData();
    const interval = setInterval(loadBillingData, 30000);
    return () => clearInterval(interval);
  }, [loadBillingData, view]);

  async function runPendingAction() {
    if (!pending) return;
    setActionBusy(true);
    try {
      const message = await pending.run();
      setFeedback({ type: "success", message });
      await loadBillingData();
    } catch (err: any) {
      setFeedback({ type: "error", message: err?.message || "That didn't work. Please try again." });
    } finally {
      setActionBusy(false);
      setPending(null);
      setDeletingId(null);
    }
  }

  function handleClearBillingData() {
    setPending({
      title: "Delete ALL billing history?",
      body: "Every usage record for every client is removed and totals reset to zero. Invoices already created are kept. This can't be undone.",
      confirmLabel: "Delete everything",
      danger: true,
      run: async () => {
        const res = await clearBillingData();
        return `Billing history cleared (${res.deleted_count} records removed).`;
      },
    });
  }

  function handleRecalculate() {
    setPending({
      title: "Recalculate all costs?",
      body: "Every billable record is re-priced with the current price list (for example after a price correction). Invoices already created keep their amounts.",
      confirmLabel: "Recalculate",
      danger: false,
      run: async () => {
        const res = await recalculateUsageCosts();
        return `Costs recalculated for ${res.updated_count.toLocaleString()} records.`;
      },
    });
  }

  function handleDeleteChannel(channelId: string, channelName: string) {
    const cleanName = formatChannelName(channelName);
    setDeletingId(`chan-${channelId}`);
    setPending({
      title: `Delete all billing records for ${cleanName}?`,
      body: "Every usage record for this channel is removed, for all time. This can't be undone.",
      confirmLabel: "Delete",
      danger: true,
      run: async () => {
        const res = await deleteChannelBilling(channelId);
        return `Deleted ${res.deleted_count} records for ${cleanName}.`;
      },
    });
  }

  function handleDeleteUser(userId: string, userName: string) {
    const name = userName || userId;
    setDeletingId(`user-${userId}`);
    setPending({
      title: `Delete all billing records for ${name}?`,
      body: "Every usage record for this person is removed, in every channel and for all time. This can't be undone.",
      confirmLabel: "Delete",
      danger: true,
      run: async () => {
        const res = await deleteUserBilling(userId);
        return `Deleted ${res.deleted_count} records for ${name}.`;
      },
    });
  }

  function handleDeleteChannelUser(channelId: string, channelName: string, userId: string, userName: string) {
    const cleanChan = formatChannelName(channelName);
    const uName = userName || userId;
    setDeletingId(`chanuser-${channelId}-${userId}`);
    setPending({
      title: `Delete ${uName}'s records in ${cleanChan}?`,
      body: "All of this person's usage records in this channel are removed, for all time. This can't be undone.",
      confirmLabel: "Delete",
      danger: true,
      run: async () => {
        const res = await deleteChannelUserBilling(channelId, userId);
        return `Deleted ${res.deleted_count} records.`;
      },
    });
  }

  function handleDeleteLog(logId: number) {
    setDeletingId(`log-${logId}`);
    setPending({
      title: `Delete reply #${logId}?`,
      body: "This single billing record is removed. This can't be undone.",
      confirmLabel: "Delete",
      danger: true,
      run: async () => {
        await deleteSingleLog(logId);
        return `Deleted reply #${logId}.`;
      },
    });
  }

  async function handleExport() {
    setExporting(true);
    try {
      await downloadUsageCsv(activeTab, range, selectedWorkspace);
    } catch (err: any) {
      setFeedback({ type: "error", message: err?.message || "We couldn't create the CSV file." });
    } finally {
      setExporting(false);
    }
  }

  const availableWorkspaces = useMemo(() => {
    const list: Array<{ workspace_id: string; workspace_name: string }> = [];
    const seen = new Set<string>();

    const isValidWs = (wid?: string, wname?: string) => {
      if (!wid) return false;
      if (wid === "UNKNOWN" || wid.startsWith("wrkspc_")) return false;
      if (wname && wname.toLowerCase().includes("unknown workspace")) return false;
      return true;
    };

    if (billingData?.workspaces) {
      for (const w of billingData.workspaces) {
        if (isValidWs(w.workspace_id, w.workspace_name) && !seen.has(w.workspace_id)) {
          seen.add(w.workspace_id);
          list.push({ workspace_id: w.workspace_id, workspace_name: w.workspace_name });
        }
      }
    }

    const items = [...(billingData?.by_channel || []), ...(billingData?.by_user || []), ...(usageLogs || [])];
    for (const item of items) {
      const wid = item.workspace_id;
      if (wid && isValidWs(wid, item.workspace_name) && !seen.has(wid)) {
        seen.add(wid);
        list.push({ workspace_id: wid, workspace_name: item.workspace_name || wid });
      }
    }
    return list;
  }, [billingData, usageLogs]);

  const summaryStats = useMemo(() => {
    if (!billingData) {
      return {
        total_calls: 0,
        total_cost_usd: 0,
        total_tokens: 0,
        total_input_tokens: 0,
        total_output_tokens: 0,
        active_channels_count: 0,
        active_users_count: 0,
      };
    }

    if (selectedWorkspace === "ALL") {
      return {
        total_calls: billingData.total_calls,
        total_cost_usd: billingData.total_cost_usd,
        total_tokens: billingData.total_tokens,
        total_input_tokens: billingData.total_input_tokens,
        total_output_tokens: billingData.total_output_tokens,
        active_channels_count: billingData.by_channel?.length || 0,
        active_users_count: billingData.by_user?.length || 0,
      };
    }

    const wsChannels = (billingData.by_channel || []).filter((item) => item.workspace_id === selectedWorkspace);
    const wsUsers = (billingData.by_user || []).filter((item) => item.workspace_id === selectedWorkspace);
    return {
      total_calls: wsChannels.reduce((sum, item) => sum + (item.calls || 0), 0),
      total_cost_usd: wsChannels.reduce((sum, item) => sum + item.total_cost_usd, 0),
      total_tokens: wsChannels.reduce((sum, item) => sum + item.total_tokens, 0),
      total_input_tokens: wsChannels.reduce((sum, item) => sum + item.input_tokens, 0),
      total_output_tokens: wsChannels.reduce((sum, item) => sum + item.output_tokens, 0),
      active_channels_count: wsChannels.length,
      active_users_count: wsUsers.length,
    };
  }, [billingData, selectedWorkspace]);

  const query = searchQuery.toLowerCase().trim();

  const filteredChannels = (billingData?.by_channel || []).filter((item) => {
    if (selectedWorkspace !== "ALL" && item.workspace_id !== selectedWorkspace) return false;
    if (!query) return true;
    const cleanName = formatChannelName(item.channel_name);
    return (
      (item.workspace_id && item.workspace_id.toLowerCase().includes(query)) ||
      (item.workspace_name && item.workspace_name.toLowerCase().includes(query)) ||
      item.channel_name.toLowerCase().includes(query) ||
      cleanName.toLowerCase().includes(query) ||
      item.channel_id.toLowerCase().includes(query)
    );
  });

  const filteredUsers = (billingData?.by_user || []).filter((item) => {
    if (selectedWorkspace !== "ALL" && item.workspace_id !== selectedWorkspace) return false;
    if (!query) return true;
    return (
      (item.workspace_id && item.workspace_id.toLowerCase().includes(query)) ||
      (item.workspace_name && item.workspace_name.toLowerCase().includes(query)) ||
      item.user_id.toLowerCase().includes(query) ||
      (item.user_name && item.user_name.toLowerCase().includes(query))
    );
  });

  const filteredChannelUsers = (billingData?.by_channel_user || []).filter((item) => {
    if (selectedWorkspace !== "ALL" && item.workspace_id !== selectedWorkspace) return false;
    if (!query) return true;
    const cleanName = formatChannelName(item.channel_name);
    return (
      (item.workspace_id && item.workspace_id.toLowerCase().includes(query)) ||
      (item.workspace_name && item.workspace_name.toLowerCase().includes(query)) ||
      item.channel_name.toLowerCase().includes(query) ||
      cleanName.toLowerCase().includes(query) ||
      item.channel_id.toLowerCase().includes(query) ||
      item.user_id.toLowerCase().includes(query) ||
      (item.user_name && item.user_name.toLowerCase().includes(query))
    );
  });

  const filteredLogs = usageLogs.filter((log) => {
    if (selectedWorkspace !== "ALL" && log.workspace_id !== selectedWorkspace) return false;
    if (!query) return true;
    const cleanName = formatChannelName(log.channel_name);
    return (
      (log.workspace_id && log.workspace_id.toLowerCase().includes(query)) ||
      (log.workspace_name && log.workspace_name.toLowerCase().includes(query)) ||
      log.channel_name.toLowerCase().includes(query) ||
      cleanName.toLowerCase().includes(query) ||
      log.channel_id.toLowerCase().includes(query) ||
      (log.user_id && log.user_id.toLowerCase().includes(query)) ||
      (log.user_name && log.user_name.toLowerCase().includes(query)) ||
      log.model.toLowerCase().includes(query)
    );
  });

  const statCards = [
    {
      label: "Total billed",
      value: formatUsd(summaryStats.total_cost_usd),
      title: `$${summaryStats.total_cost_usd.toFixed(6)}`,
      hint: `${summaryStats.total_calls.toLocaleString()} AI replies`,
      icon: DollarSign,
      tone: "bg-emerald-50 text-emerald-600 border-emerald-200",
    },
    {
      label: "Tokens used",
      value: summaryStats.total_tokens.toLocaleString(),
      hint: `${summaryStats.total_input_tokens.toLocaleString()} read · ${summaryStats.total_output_tokens.toLocaleString()} written`,
      icon: Cpu,
      tone: "bg-gray-100 text-[#088ADA] border-gray-200",
    },
    {
      label: "Channels",
      value: summaryStats.active_channels_count.toLocaleString(),
      hint: "Slack channels that used the AI",
      icon: Layers,
      tone: "bg-gray-100 text-[#088ADA] border-gray-200",
    },
    {
      label: "People",
      value: summaryStats.active_users_count.toLocaleString(),
      hint: "People who asked the AI something",
      icon: Users,
      tone: "bg-gray-100 text-[#088ADA] border-gray-200",
    },
  ];

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <PageHeader
        icon={Receipt}
        title="Usage & billing"
        description={
          isMasterAdmin
            ? "What the AI assistant cost per client, channel and person, and the invoices you send. Only replies on the JTS key are billed."
            : "What your team's AI usage cost, and your invoices. Replies made with your own Anthropic key are not billed."
        }
        actions={
          view === "usage" ? (
            <>
              <button onClick={loadBillingData} disabled={loading} className={btn.secondary}>
                <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                <span>Refresh</span>
              </button>
              {isMasterAdmin && (
                <>
                  <button onClick={handleRecalculate} disabled={loading} className={btn.secondary} title="Re-price all records with the current price list">
                    <Calculator className="h-3.5 w-3.5" />
                    <span>Recalculate costs</span>
                  </button>
                  <button onClick={handleClearBillingData} disabled={loading} className={btn.dangerSoft}>
                    <Trash2 className="h-3.5 w-3.5" />
                    <span>Clear all</span>
                  </button>
                </>
              )}
            </>
          ) : undefined
        }
      />

      {/* Client: own Anthropic key (not billed) vs. JTS key (billed) */}
      {!isMasterAdmin && myFolderId && <ClientApiKeyCard folderId={myFolderId} canEdit={userRole === "client_admin"} />}

      {canSeeInvoices && (
        <Tabs
          value={view}
          onChange={setView}
          tabs={[
            { id: "usage" as const, label: "Usage", icon: BarChart3 },
            { id: "invoices" as const, label: "Invoices", icon: FileText },
          ]}
        />
      )}

      {feedback && (
        <Alert type={feedback.type} onClose={() => setFeedback(null)}>
          {feedback.message}
        </Alert>
      )}

      {view === "invoices" && canSeeInvoices && <InvoicesPanel isMasterAdmin={isMasterAdmin} />}

      {view === "usage" && (
        <>
      {/* Period picker */}
      <div className="bg-white p-4 rounded-2xl border border-gray-200 shadow-sm flex flex-col lg:flex-row lg:items-center justify-between gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <CalendarRange className="h-4 w-4 text-[#088ADA]" />
          <span className="text-xs font-semibold text-gray-700">Period</span>
          <select
            value={preset}
            onChange={(e) => {
              const next = e.target.value as PeriodPreset;
              if (next === "custom") setCustomRange(presetToRange(preset, customRange));
              setPreset(next);
            }}
            className="bg-white border border-gray-300 text-xs font-semibold text-gray-800 rounded-lg px-2.5 py-1.5 focus:outline-none focus:border-[#088ADA]"
          >
            {PERIOD_OPTIONS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
          {preset === "custom" && (
            <>
              <input
                type="date"
                value={customRange.start || ""}
                onChange={(e) => setCustomRange((r) => ({ ...r, start: e.target.value || undefined }))}
                className="border border-gray-300 rounded-lg px-2 py-1 text-xs"
                aria-label="From date"
              />
              <span className="text-xs text-gray-500">to</span>
              <input
                type="date"
                value={customRange.end || ""}
                onChange={(e) => setCustomRange((r) => ({ ...r, end: e.target.value || undefined }))}
                className="border border-gray-300 rounded-lg px-2 py-1 text-xs"
                aria-label="To date"
              />
            </>
          )}
          <span className="text-xs text-gray-500" suppressHydrationWarning>
            {rangeLabel} <span className="text-gray-400">(UTC)</span>
          </span>
        </div>
        <button onClick={handleExport} disabled={exporting || loading || !rangeValid} className={btn.secondary} title="Download the table below as a CSV file">
          <Download className="h-3.5 w-3.5" />
          {exporting ? "Preparing..." : "Export CSV"}
        </button>
      </div>

      {!rangeValid && <Alert type="warning">The end date must be on or after the start date.</Alert>}
      {loadError && <Alert type="error">{loadError}</Alert>}

      {/* Summary KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {statCards.map((c) => (
          <div key={c.label} className="bg-white border border-gray-200 p-5 rounded-2xl space-y-2 shadow-sm">
            <div className="flex items-center justify-between text-xs text-gray-600 font-semibold">
              <span>{c.label}</span>
              <div className={`p-2 rounded-lg border ${c.tone}`}>
                <c.icon className="h-4 w-4" />
              </div>
            </div>
            <div className="text-2xl font-bold font-mono text-gray-900" title={c.title}>
              {c.value}
            </div>
            <p className="text-xs text-gray-500">{c.hint}</p>
          </div>
        ))}
      </div>

      {/* Controls & Filter Bar */}
      <div className="bg-white p-4 rounded-2xl border border-gray-200 shadow-sm flex flex-col gap-4">
        {/* Navigation Tabs */}
        <Tabs
          value={activeTab}
          onChange={setActiveTab}
          className="w-full"
          tabs={[
            { id: "channel" as const, label: "By channel", icon: FileText },
            { id: "user" as const, label: "By person", icon: User },
            { id: "channel_user" as const, label: "Person per channel", icon: Users },
            { id: "logs" as const, label: "Every reply", icon: Zap },
          ]}
        />

        {/* Workspace Dropdown & Instant Search */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 w-full">
          {isMasterAdmin && (
            <div className="flex items-center gap-1.5 text-xs font-semibold text-gray-700 bg-gray-50 border border-gray-200 px-3 py-1.5 rounded-xl shrink-0">
              <Building2 className="h-4 w-4 text-[#088ADA]" />
              <span>Workspace:</span>
              <select
                value={selectedWorkspace}
                onChange={(e) => setSelectedWorkspace(e.target.value)}
                className="bg-white border border-gray-300 text-xs font-bold text-gray-800 rounded-lg px-2.5 py-1 focus:outline-none focus:border-[#088ADA] shadow-sm ml-1 cursor-pointer"
              >
                <option value="ALL">All workspaces</option>
                {availableWorkspaces.map((ws) => {
                  const displayName = ws.workspace_name.includes(ws.workspace_id)
                    ? ws.workspace_name
                    : `${ws.workspace_name} (${ws.workspace_id})`;
                  return (
                    <option key={ws.workspace_id} value={ws.workspace_id}>
                      {displayName}
                    </option>
                  );
                })}
              </select>
            </div>
          )}

          <div className="relative w-full sm:w-64">
            <Search className="h-4 w-4 text-gray-400 absolute left-3.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Search channel or person"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="w-full pl-10 pr-4 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs text-gray-700 placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:bg-white transition"
            />
          </div>
        </div>
      </div>

      {activeTab === "logs" && usageLogs.length >= LOG_LIMIT && (
        <Alert type="info">
          Showing the latest {LOG_LIMIT.toLocaleString()} replies in this period. Choose a shorter period, or use Export CSV
          (up to 5,000 replies). The totals above always include every reply.
        </Alert>
      )}

      {/* 1. BILLING BY CHANNEL TABLE */}
      {activeTab === "channel" && (
        <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
          <div className="p-4 border-b border-gray-200 bg-gray-50">
            <h2 className="text-sm font-bold text-gray-800 flex items-center gap-2">
              <FileText className="h-4 w-4 text-[#088ADA]" />
              <span>Cost by channel</span>
            </h2>
          </div>
          <DataTable
            rows={filteredChannels}
            rowKey={(item, idx) => `${item.workspace_id}-${item.channel_id}-${idx}`}
            searchable={false}
            itemLabel="channels"
            initialSort={{ key: "cost", dir: "desc" }}
            emptyMessage="No usage found. Try another search or workspace."
            columns={[
              ...workspaceColumns<(typeof filteredChannels)[number]>(isMasterAdmin),
              {
                key: "channel_name",
                header: "Channel",
                className: "font-semibold text-[#088ADA]",
                render: (item) => formatChannelName(item.channel_name),
              },
              { key: "channel_id", header: "Channel ID", className: "font-mono text-gray-600" },
              { key: "calls", header: "AI replies", align: "right", className: "font-mono font-bold text-gray-700" },
              ...tokenColumns<(typeof filteredChannels)[number]>((item) => item.total_cost_usd),
              ...deleteColumn<(typeof filteredChannels)[number]>(isMasterAdmin, (item) => (
                <button
                  onClick={() => handleDeleteChannel(item.channel_id, item.channel_name)}
                  disabled={deletingId === `chan-${item.channel_id}`}
                  className={deleteBtnClass}
                  title="Delete all billing records for this channel"
                >
                  <Trash2 className="h-3 w-3" />
                  <span>Delete</span>
                </button>
              )),
            ]}
          />
        </div>
      )}

      {/* 2. BILLING BY USER TABLE */}
      {activeTab === "user" && (
        <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
          <div className="p-4 border-b border-gray-200 bg-gray-50">
            <h2 className="text-sm font-bold text-gray-800 flex items-center gap-2">
              <User className="h-4 w-4 text-[#088ADA]" />
              <span>Cost by person</span>
            </h2>
          </div>
          <DataTable
            rows={filteredUsers}
            rowKey={(item, idx) => `${item.workspace_id}-${item.user_id}-${idx}`}
            searchable={false}
            itemLabel="people"
            initialSort={{ key: "cost", dir: "desc" }}
            emptyMessage="No usage found. Try another search or workspace."
            columns={[
              ...workspaceColumns<(typeof filteredUsers)[number]>(isMasterAdmin),
              {
                key: "user_name",
                header: "Person",
                className: "font-semibold text-gray-800",
                sortValue: (item) => (item.user_name || item.user_id || "").toLowerCase(),
                render: (item) => item.user_name || item.user_id,
              },
              { key: "user_id", header: "Slack user ID", className: "font-mono text-gray-600" },
              { key: "calls", header: "AI replies", align: "right", className: "font-mono font-bold text-gray-700" },
              ...tokenColumns<(typeof filteredUsers)[number]>((item) => item.total_cost_usd),
              ...deleteColumn<(typeof filteredUsers)[number]>(isMasterAdmin, (item) => (
                <button
                  onClick={() => handleDeleteUser(item.user_id, item.user_name || item.user_id)}
                  disabled={deletingId === `user-${item.user_id}`}
                  className={deleteBtnClass}
                  title="Delete all billing records for this person"
                >
                  <Trash2 className="h-3 w-3" />
                  <span>Delete</span>
                </button>
              )),
            ]}
          />
        </div>
      )}

      {/* 3. USER BREAKDOWN PER CHANNEL TABLE */}
      {activeTab === "channel_user" && (
        <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
          <div className="p-4 border-b border-gray-200 bg-gray-50">
            <h2 className="text-sm font-bold text-gray-800 flex items-center gap-2">
              <Users className="h-4 w-4 text-[#088ADA]" />
              <span>Cost per person in each channel</span>
            </h2>
          </div>
          <DataTable
            rows={filteredChannelUsers}
            rowKey={(item, idx) => `${item.workspace_id}-${item.channel_id}-${item.user_id}-${idx}`}
            searchable={false}
            itemLabel="rows"
            initialSort={{ key: "cost", dir: "desc" }}
            emptyMessage="No usage found. Try another search or workspace."
            columns={[
              ...workspaceColumns<(typeof filteredChannelUsers)[number]>(isMasterAdmin),
              {
                key: "channel_name",
                header: "Channel",
                className: "font-semibold text-[#088ADA]",
                render: (item) => formatChannelName(item.channel_name),
              },
              { key: "channel_id", header: "Channel ID", className: "font-mono text-gray-600" },
              {
                key: "user_name",
                header: "Person",
                className: "font-semibold text-gray-800",
                sortValue: (item) => (item.user_name || item.user_id || "").toLowerCase(),
                render: (item) => item.user_name || item.user_id,
              },
              { key: "user_id", header: "Slack user ID", className: "font-mono text-gray-600" },
              { key: "calls", header: "AI replies", align: "right", className: "font-mono font-bold text-gray-700" },
              ...tokenColumns<(typeof filteredChannelUsers)[number]>((item) => item.total_cost_usd),
              ...deleteColumn<(typeof filteredChannelUsers)[number]>(isMasterAdmin, (item) => (
                <button
                  onClick={() =>
                    handleDeleteChannelUser(item.channel_id, item.channel_name, item.user_id, item.user_name || item.user_id)
                  }
                  disabled={deletingId === `chanuser-${item.channel_id}-${item.user_id}`}
                  className={deleteBtnClass}
                  title="Delete records for this person in this channel"
                >
                  <Trash2 className="h-3 w-3" />
                  <span>Delete</span>
                </button>
              )),
            ]}
          />
        </div>
      )}

      {/* 4. CALL HISTORY LOGS TABLE */}
      {activeTab === "logs" && (
        <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
          <div className="p-4 border-b border-gray-200 bg-gray-50">
            <h2 className="text-sm font-bold text-gray-800 flex items-center gap-2">
              <Zap className="h-4 w-4 text-[#088ADA]" />
              <span>Every billed AI reply</span>
            </h2>
          </div>
          <DataTable
            rows={filteredLogs}
            rowKey={(log) => log.id}
            searchable={false}
            itemLabel="replies"
            initialSort={{ key: "created_at", dir: "desc" }}
            emptyMessage="No billed replies yet."
            columns={[
              ...workspaceColumns<ApiUsageLog>(isMasterAdmin),
              { key: "id", header: "#", className: "font-mono text-gray-500", render: (log) => `#${log.id}` },
              {
                key: "created_at",
                header: "Time",
                className: "text-gray-600 font-mono whitespace-nowrap",
                sortValue: (log) => (log.created_at ? new Date(log.created_at).getTime() : null),
                searchValue: () => "",
                render: (log) => formatLocalDateTime(log.created_at),
              },
              {
                key: "channel_name",
                header: "Channel",
                className: "font-semibold text-[#088ADA]",
                render: (log) => formatChannelName(log.channel_name),
              },
              {
                key: "user_name",
                header: "Person",
                className: "font-semibold text-gray-800",
                sortValue: (log) => (log.user_name || log.user_id || "").toLowerCase(),
                render: (log) => log.user_name || log.user_id || "unknown",
              },
              {
                key: "user_id",
                header: "Slack user ID",
                className: "font-mono text-gray-600",
                render: (log) => log.user_id || "-",
              },
              {
                key: "model",
                header: "Model",
                render: (log) => (
                  <span className="font-mono text-[11px] text-gray-700 bg-gray-100 px-2 py-0.5 rounded border border-gray-200">
                    {log.model}
                  </span>
                ),
              },
              ...tokenColumns<ApiUsageLog>((log) => log.cost_usd),
              ...deleteColumn<ApiUsageLog>(isMasterAdmin, (log) => (
                <button
                  onClick={() => handleDeleteLog(log.id)}
                  disabled={deletingId === `log-${log.id}`}
                  className={deleteBtnClass}
                  title="Delete this entry"
                >
                  <Trash2 className="h-3 w-3" />
                  <span>Delete</span>
                </button>
              )),
            ]}
          />
        </div>
      )}
        </>
      )}

      <ConfirmDialog
        open={Boolean(pending)}
        busy={actionBusy}
        title={pending?.title || ""}
        confirmLabel={pending?.confirmLabel}
        confirmClass={pending?.danger ? btn.danger : btn.primary}
        onCancel={() => {
          setPending(null);
          setDeletingId(null);
        }}
        onConfirm={runPendingAction}
      >
        {pending?.body && <p>{pending.body}</p>}
      </ConfirmDialog>
    </div>
  );
}
