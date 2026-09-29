"use client";

import { useEffect, useState, useMemo } from "react";
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
} from "lucide-react";
import { UsageSummary, ApiUsageLog, formatLocalDateTime, isClientKeyMessage } from "@/lib/types";
import { ClientApiKeyCard } from "@/components/ClientApiKeyCard";
import { PageHeader, btn } from "@/components/ui";
import { DataTable, Column } from "@/components/DataTable";
import {
  fetchBillingSummary,
  fetchUsageLogs,
  clearBillingData,
  deleteChannelBilling,
  deleteUserBilling,
  deleteChannelUserBilling,
  deleteSingleLog,
  fetchFolder,
  fetchFolders,
  fetchChannelMessages,
  getAuthoritativeWorkspace,
  AUTHORITATIVE_CHANNEL_NAMES,
} from "@/lib/api";

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
  const [clearing, setClearing] = useState(false);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedWorkspace, setSelectedWorkspace] = useState<string>("ALL");
  const [activeTab, setActiveTab] = useState<"channel" | "user" | "channel_user" | "logs">("channel");

  // RBAC state
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
      } catch {}
    }
  }, []);

  const isMasterAdmin = !userRole || userRole === "admin" || userRole === "jts_admin";

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

  async function loadBillingData() {
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

      const isClient = activeRole === "client_admin" || activeRole === "client_standard";

      if (isClient) {
        // CLIENT ADMIN DATA (Directly fetched from channels inside client folder)
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

        const [sumRes, logListRes] = await Promise.all([
          fetchBillingSummary().catch(() => null),
          fetchUsageLogs(150).catch(() => []),
        ]);

        const matchesFolderChannel = (cId?: string, cName?: string) => {
          if (!cId && !cName) return false;
          const cleanId = (cId || "").toLowerCase().replace(/^[@#]/, "");
          const cleanName = (cName || "").toLowerCase().replace(/^[@#]/, "");
          if (channelIdSet.size === 0) return true;
          return (
            channelIdSet.has((cId || "").toLowerCase()) ||
            channelIdSet.has(cleanId) ||
            channelIdSet.has((cName || "").toLowerCase()) ||
            channelIdSet.has(cleanName)
          );
        };

        const byChannelList: any[] = [];
        let totalFolderInTokens = 0;
        let totalFolderOutTokens = 0;
        let totalFolderTokens = 0;
        let totalFolderCost = 0;
        let totalFolderCalls = 0;

        await Promise.all(
          folderChannels.map(async (ch) => {
            let chIn = 0;
            let chOut = 0;
            let chTot = 0;
            let chCost = 0;
            let chCalls = 0;

            try {
              const msgRes = await fetchChannelMessages(ch.channel_id, 250);
              const msgs = msgRes.messages || [];
              msgs.forEach((m) => {
                const isBot =
                  m.role === "assistant" ||
                  m.user_id === "bot" ||
                  (m.user_name && (m.user_name.includes("Assistant") || m.user_name.includes("bot") || m.user_name.includes("Agent")));

                if (isBot && !isClientKeyMessage(m)) {
                  chCalls += 1;
                  let inTok = m.input_tokens || 0;
                  let outTok = m.output_tokens || 0;
                  let totTok = m.total_tokens || 0;
                  let cUsd = m.cost_usd || 0;

                  if (!totTok || !cUsd) {
                    const contentText = m.content || "";
                    outTok = Math.max(20, Math.floor(contentText.length / 3.8));
                    inTok = Math.max(250, Math.floor(outTok * 2.5));
                    totTok = totTok || (inTok + outTok);
                    cUsd = cUsd || Number(((inTok * 3.0 / 1000000) + (outTok * 15.0 / 1000000)).toFixed(6));
                  }

                  chIn += inTok;
                  chOut += outTok;
                  chTot += totTok;
                  chCost += cUsd;
                }
              });
            } catch (err) {
              console.warn(`Could not fetch channel ${ch.channel_id} messages:`, err);
            }

            const logCh = sumRes?.by_channel?.find((bc: any) => matchesFolderChannel(bc.channel_id, bc.channel_name));
            if (logCh) {
              chIn = Math.max(chIn, logCh.input_tokens || 0);
              chOut = Math.max(chOut, logCh.output_tokens || 0);
              chTot = Math.max(chTot, logCh.total_tokens || 0);
              chCost = Math.max(chCost, logCh.total_cost_usd || 0);
              chCalls = Math.max(chCalls, logCh.calls || 0);
            }

            totalFolderInTokens += chIn;
            totalFolderOutTokens += chOut;
            totalFolderTokens += chTot;
            totalFolderCost += chCost;
            totalFolderCalls += chCalls;

            byChannelList.push({
              workspace_id: ch.workspace_id || "",
              workspace_name: ch.workspace_name || "",
              channel_id: ch.channel_id,
              channel_name: ch.channel_name || `#${ch.channel_id}`,
              calls: chCalls,
              input_tokens: chIn,
              output_tokens: chOut,
              total_tokens: chTot,
              total_cost_usd: Number(chCost.toFixed(6)),
            });
          })
        );

        const filteredLogs = (logListRes || []).filter((l: any) => matchesFolderChannel(l.channel_id, l.channel_name));
        const uniqueLogs = Array.from(
          new Map((filteredLogs || []).map((item: any) => [item.id, item])).values()
        );

        setUsageLogs(uniqueLogs as ApiUsageLog[]);
        setBillingData({
          total_calls: totalFolderCalls,
          total_input_tokens: totalFolderInTokens,
          total_output_tokens: totalFolderOutTokens,
          total_tokens: totalFolderTokens,
          total_cost_usd: Number(totalFolderCost.toFixed(6)),
          active_channels_count: folderChannels.length,
          active_users_count: 1,
          by_channel: byChannelList,
          by_user: (sumRes?.by_user || []).filter((u: any) => matchesFolderChannel(u.channel_id)),
          by_channel_user: (sumRes?.by_channel_user || []).filter((cu: any) => matchesFolderChannel(cu.channel_id, cu.channel_name)),
        });

      } else {
        // JTS MASTER ADMIN DATA (Global Telemetry - Consolidated & Unified)
        const [sum, logList] = await Promise.all([
          fetchBillingSummary(),
          fetchUsageLogs(150),
        ]);

        if (sum) {
          // Consolidate by Canonical Channel ID: each channel ID appears EXACTLY ONCE
          const chanMap = new Map<string, any>();
          (sum.by_channel || []).forEach((item: any) => {
            const rawWs = item.workspace_id || item.workspace_name || "";
            const cid = canonicalChannelId(item.channel_id, rawWs, item.channel_name);
            item.channel_id = cid;

            const authWs = getAuthoritativeWorkspace(cid, item.workspace_id, item.workspace_name);
            item.workspace_id = authWs.id;
            item.workspace_name = authWs.name;

            const cleanCid = cid.toUpperCase();
            if (AUTHORITATIVE_CHANNEL_NAMES[cleanCid]) {
              item.channel_name = AUTHORITATIVE_CHANNEL_NAMES[cleanCid];
            }

            const key = cleanCid;
            if (!key) return;

            if (!chanMap.has(key)) {
              chanMap.set(key, { ...item });
            } else {
              const existing = chanMap.get(key);
              existing.calls = (existing.calls || 0) + (item.calls || 0);
              existing.input_tokens = (existing.input_tokens || 0) + (item.input_tokens || 0);
              existing.output_tokens = (existing.output_tokens || 0) + (item.output_tokens || 0);
              existing.total_tokens = (existing.total_tokens || 0) + (item.total_tokens || 0);
              existing.total_cost_usd = Number(((existing.total_cost_usd || 0) + (item.total_cost_usd || 0)).toFixed(6));
              existing.workspace_id = authWs.id;
              existing.workspace_name = authWs.name;
              if (AUTHORITATIVE_CHANNEL_NAMES[cleanCid]) {
                existing.channel_name = AUTHORITATIVE_CHANNEL_NAMES[cleanCid];
              }
            }
          });
          sum.by_channel = Array.from(chanMap.values()).sort((a, b) => b.total_cost_usd - a.total_cost_usd);

          // Consolidate by User ID / User Name: each user appears EXACTLY ONCE
          const userMap = new Map<string, any>();
          (sum.by_user || []).forEach((item: any) => {
            const uId = (item.user_id || "").trim();
            const uName = (item.user_name || "").trim();
            if (uId === "U0AQUL5KQMA" || uName.toLowerCase().includes("admin user")) {
              item.workspace_id = "T5ZMF56H5";
              item.workspace_name = "Axcel World";
            }
            const key = (uId || uName).toUpperCase();
            if (!key) return;

            if (!userMap.has(key)) {
              userMap.set(key, { ...item });
            } else {
              const existing = userMap.get(key);
              existing.calls = (existing.calls || 0) + (item.calls || 0);
              existing.input_tokens = (existing.input_tokens || 0) + (item.input_tokens || 0);
              existing.output_tokens = (existing.output_tokens || 0) + (item.output_tokens || 0);
              existing.total_tokens = (existing.total_tokens || 0) + (item.total_tokens || 0);
              existing.total_cost_usd = Number(((existing.total_cost_usd || 0) + (item.total_cost_usd || 0)).toFixed(6));
              if (item.workspace_id) {
                existing.workspace_id = item.workspace_id;
                existing.workspace_name = item.workspace_name;
              }
            }
          });
          sum.by_user = Array.from(userMap.values()).sort((a, b) => b.total_cost_usd - a.total_cost_usd);

          // Consolidate by Channel ID + User ID
          const cuMap = new Map<string, any>();
          (sum.by_channel_user || []).forEach((item: any) => {
            const rawWs = item.workspace_id || item.workspace_name || "";
            const cid = canonicalChannelId(item.channel_id, rawWs, item.channel_name);
            item.channel_id = cid;

            const authWs = getAuthoritativeWorkspace(cid, item.workspace_id, item.workspace_name);
            const uId = (item.user_id || "").trim();
            const uName = (item.user_name || "").trim();

            if (uId === "U0AQUL5KQMA" || uName.toLowerCase().includes("admin user")) {
              item.workspace_id = "T5ZMF56H5";
              item.workspace_name = "Axcel World";
            } else {
              item.workspace_id = authWs.id;
              item.workspace_name = authWs.name;
            }

            const cleanCid = cid.toUpperCase();
            if (AUTHORITATIVE_CHANNEL_NAMES[cleanCid]) {
              item.channel_name = AUTHORITATIVE_CHANNEL_NAMES[cleanCid];
            }

            const cKey = cleanCid;
            const uKey = (uId || uName).toUpperCase();
            const key = `${cKey}::${uKey}`;
            if (!key) return;

            if (!cuMap.has(key)) {
              cuMap.set(key, { ...item });
            } else {
              const existing = cuMap.get(key);
              existing.calls = (existing.calls || 0) + (item.calls || 0);
              existing.input_tokens = (existing.input_tokens || 0) + (item.input_tokens || 0);
              existing.output_tokens = (existing.output_tokens || 0) + (item.output_tokens || 0);
              existing.total_tokens = (existing.total_tokens || 0) + (item.total_tokens || 0);
              existing.total_cost_usd = Number(((existing.total_cost_usd || 0) + (item.total_cost_usd || 0)).toFixed(6));
              existing.workspace_id = item.workspace_id;
              existing.workspace_name = item.workspace_name;
              if (AUTHORITATIVE_CHANNEL_NAMES[cleanCid]) {
                existing.channel_name = AUTHORITATIVE_CHANNEL_NAMES[cleanCid];
              }
            }
          });
          sum.by_channel_user = Array.from(cuMap.values()).sort((a, b) => b.total_cost_usd - a.total_cost_usd);

          // Workspaces consolidation
          if (sum.workspaces) {
            const wsMap = new Map<string, any>();
            sum.workspaces.forEach((w: any) => {
              if (w.workspace_id && w.workspace_id !== "UNKNOWN" && !w.workspace_id.startsWith("wrkspc_")) {
                wsMap.set(w.workspace_id, w);
              }
            });
            if (!wsMap.has("T5ZMF56H5")) {
              wsMap.set("T5ZMF56H5", { workspace_id: "T5ZMF56H5", workspace_name: "Axcel World" });
            }
            if (!wsMap.has("T02HKMBE09K")) {
              wsMap.set("T02HKMBE09K", { workspace_id: "T02HKMBE09K", workspace_name: "JTS Team" });
            }
            sum.workspaces = Array.from(wsMap.values());
          }
        }

        setBillingData(sum);
        const mappedLogs = (logList || []).map((item: any) => {
          const rawWs = item.workspace_id || item.workspace_name || "";
          const cid = canonicalChannelId(item.channel_id, rawWs, item.channel_name);
          const authWs = getAuthoritativeWorkspace(cid, item.workspace_id, item.workspace_name);
          const uId = (item.user_id || "").trim();
          const uName = (item.user_name || "").trim();
          let wid = authWs.id;
          let wname = authWs.name;
          if (uId === "U0AQUL5KQMA" || uName.toLowerCase().includes("admin user")) {
            wid = "T5ZMF56H5";
            wname = "Axcel World";
          }
          let cname = item.channel_name;
          if (AUTHORITATIVE_CHANNEL_NAMES[cid.toUpperCase()]) {
            cname = AUTHORITATIVE_CHANNEL_NAMES[cid.toUpperCase()];
          }
          return {
            ...item,
            channel_id: cid,
            channel_name: cname,
            workspace_id: wid,
            workspace_name: wname,
          };
        });
        const uniqueLogs = Array.from(
          new Map(mappedLogs.map((item: any) => [item.id, item])).values()
        );
        setUsageLogs(uniqueLogs);
      }
    } catch (err) {
      console.error("Failed to load billing telemetry:", err);
    } finally {
      setLoading(false);
    }
  }

  async function handleClearBillingData() {
    if (!window.confirm("Are you sure you want to clear ALL billing history and reset token cost logs? This action cannot be undone.")) {
      return;
    }

    setClearing(true);
    try {
      const res = await clearBillingData();
      alert(`Billing history cleared successfully! (${res.deleted_count} log records removed)`);
      await loadBillingData();
    } catch (err: any) {
      alert(`Error clearing billing data: ${err.message || "Failed to clear"}`);
    } finally {
      setClearing(false);
    }
  }

  async function handleDeleteChannel(channelId: string, channelName: string) {
    const cleanName = formatChannelName(channelName);
    if (!window.confirm(`Are you sure you want to delete all billing telemetry data for channel '${cleanName}' (${channelId})?`)) {
      return;
    }

    setDeletingId(`chan-${channelId}`);
    try {
      const res = await deleteChannelBilling(channelId);
      alert(`Deleted ${res.deleted_count} billing records for channel ${cleanName}.`);
      await loadBillingData();
    } catch (err: any) {
      alert(`Failed to delete channel billing data: ${err.message || "Error occurred"}`);
    } finally {
      setDeletingId(null);
    }
  }

  async function handleDeleteUser(userId: string, userName: string) {
    const name = userName || userId;
    if (!window.confirm(`Are you sure you want to delete all billing telemetry data for user '${name}' (${userId})?`)) {
      return;
    }

    setDeletingId(`user-${userId}`);
    try {
      const res = await deleteUserBilling(userId);
      alert(`Deleted ${res.deleted_count} billing records for user ${name}.`);
      await loadBillingData();
    } catch (err: any) {
      alert(`Failed to delete user billing data: ${err.message || "Error occurred"}`);
    } finally {
      setDeletingId(null);
    }
  }

  async function handleDeleteChannelUser(channelId: string, channelName: string, userId: string, userName: string) {
    const cleanChan = formatChannelName(channelName);
    const uName = userName || userId;
    if (!window.confirm(`Are you sure you want to delete billing records for user '${uName}' in channel '${cleanChan}'?`)) {
      return;
    }

    setDeletingId(`chanuser-${channelId}-${userId}`);
    try {
      const res = await deleteChannelUserBilling(channelId, userId);
      alert(`Deleted ${res.deleted_count} billing records for user ${uName} in channel ${cleanChan}.`);
      await loadBillingData();
    } catch (err: any) {
      alert(`Failed to delete entry: ${err.message || "Error occurred"}`);
    } finally {
      setDeletingId(null);
    }
  }

  async function handleDeleteLog(logId: number) {
    if (!window.confirm(`Are you sure you want to delete API call log record #${logId}?`)) {
      return;
    }

    setDeletingId(`log-${logId}`);
    try {
      await deleteSingleLog(logId);
      await loadBillingData();
    } catch (err: any) {
      alert(`Failed to delete log record: ${err.message || "Error occurred"}`);
    } finally {
      setDeletingId(null);
    }
  }

  useEffect(() => {
    loadBillingData();
    const interval = setInterval(loadBillingData, 12000);
    return () => clearInterval(interval);
  }, []);

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

    const items = [
      ...(billingData?.by_channel || []),
      ...(billingData?.by_user || []),
      ...(usageLogs || []),
    ];
    for (const item of items) {
      const wid = item.workspace_id;
      if (wid && isValidWs(wid, item.workspace_name) && !seen.has(wid)) {
        seen.add(wid);
        list.push({
          workspace_id: wid,
          workspace_name: item.workspace_name || wid,
        });
      }
    }
    return list;
  }, [billingData, usageLogs]);

  const summaryStats = useMemo(() => {
    if (!billingData) {
      return {
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
        total_cost_usd: billingData.total_cost_usd,
        total_tokens: billingData.total_tokens,
        total_input_tokens: billingData.total_input_tokens,
        total_output_tokens: billingData.total_output_tokens,
        active_channels_count: billingData.active_channels_count || billingData.by_channel?.length || 0,
        active_users_count: billingData.active_users_count || billingData.by_user?.length || 0,
      };
    }

    const wsChannels = (billingData.by_channel || []).filter((item) => item.workspace_id === selectedWorkspace);
    const wsUsers = (billingData.by_user || []).filter((item) => item.workspace_id === selectedWorkspace);
    const totalCost = wsChannels.reduce((sum, item) => sum + item.total_cost_usd, 0);
    const totalToks = wsChannels.reduce((sum, item) => sum + item.total_tokens, 0);
    const totalIn = wsChannels.reduce((sum, item) => sum + item.input_tokens, 0);
    const totalOut = wsChannels.reduce((sum, item) => sum + item.output_tokens, 0);

    return {
      total_cost_usd: totalCost,
      total_tokens: totalToks,
      total_input_tokens: totalIn,
      total_output_tokens: totalOut,
      active_channels_count: wsChannels.length,
      active_users_count: wsUsers.length,
    };
  }, [billingData, selectedWorkspace]);

  const query = searchQuery.toLowerCase().trim();

  const filteredChannels = (billingData?.by_channel || []).filter((item) => {
    if (selectedWorkspace !== "ALL" && item.workspace_id !== selectedWorkspace) {
      return false;
    }
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
    if (selectedWorkspace !== "ALL" && item.workspace_id !== selectedWorkspace) {
      return false;
    }
    if (!query) return true;
    return (
      (item.workspace_id && item.workspace_id.toLowerCase().includes(query)) ||
      (item.workspace_name && item.workspace_name.toLowerCase().includes(query)) ||
      item.user_id.toLowerCase().includes(query) ||
      (item.user_name && item.user_name.toLowerCase().includes(query))
    );
  });

  const filteredChannelUsers = (billingData?.by_channel_user || []).filter((item) => {
    if (selectedWorkspace !== "ALL" && item.workspace_id !== selectedWorkspace) {
      return false;
    }
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
    if (selectedWorkspace !== "ALL" && log.workspace_id !== selectedWorkspace) {
      return false;
    }
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

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <PageHeader
        icon={Receipt}
        title="Usage & billing"
        description={
          isMasterAdmin
            ? "How much the AI assistant cost, per Slack workspace, channel and person. Only replies on the JTS key are billed."
            : "How much your team's AI usage cost. Replies made with your own Anthropic key are not billed."
        }
        actions={
          <>
            <button onClick={loadBillingData} disabled={loading || clearing} className={btn.secondary}>
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              <span>Refresh</span>
            </button>
            {isMasterAdmin && (
              <button onClick={handleClearBillingData} disabled={loading || clearing} className={btn.dangerSoft}>
                <Trash2 className="h-3.5 w-3.5" />
                <span>{clearing ? "Clearing..." : "Clear all billing data"}</span>
              </button>
            )}
          </>
        }
      />

      {/* Client: own Anthropic key (not billed) vs. JTS key (billed) */}
      {!isMasterAdmin && myFolderId && (
        <ClientApiKeyCard folderId={myFolderId} canEdit={userRole === "client_admin"} />
      )}

      {/* Summary KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Total Cost */}
        <div className="bg-white border border-gray-200 p-5 rounded-2xl space-y-2 shadow-sm">
          <div className="flex items-center justify-between text-xs text-gray-600 font-semibold">
            <span>Total billed</span>
            <div className="p-2 rounded-lg bg-emerald-50 text-emerald-600 border border-emerald-200">
              <DollarSign className="h-4 w-4" />
            </div>
          </div>
          <div className="text-2xl font-bold font-mono text-gray-900">
            ${summaryStats.total_cost_usd.toFixed(6)}
          </div>
          <p className="text-xs text-gray-500">In US dollars, based on each AI model's price</p>
        </div>

        {/* Total Tokens */}
        <div className="bg-white border border-gray-200 p-5 rounded-2xl space-y-2 shadow-sm">
          <div className="flex items-center justify-between text-xs text-gray-600 font-semibold">
            <span>Tokens used</span>
            <div className="p-2 rounded-lg bg-gray-100 text-[#088ADA] border border-gray-200">
              <Cpu className="h-4 w-4" />
            </div>
          </div>
          <div className="text-2xl font-bold font-mono text-gray-900">
            {summaryStats.total_tokens.toLocaleString()}
          </div>
          <p className="text-xs text-gray-500">
            {`${summaryStats.total_input_tokens.toLocaleString()} read · ${summaryStats.total_output_tokens.toLocaleString()} written`}
          </p>
        </div>

        {/* Active Channels */}
        <div className="bg-white border border-gray-200 p-5 rounded-2xl space-y-2 shadow-sm">
          <div className="flex items-center justify-between text-xs text-gray-600 font-semibold">
            <span>Channels</span>
            <div className="p-2 rounded-lg bg-gray-100 text-[#088ADA] border border-gray-200">
              <Layers className="h-4 w-4" />
            </div>
          </div>
          <div className="text-2xl font-bold font-mono text-gray-900">
            {summaryStats.active_channels_count}
          </div>
          <p className="text-xs text-gray-500">Slack channels that used the AI</p>
        </div>

        {/* Active Users */}
        <div className="bg-white border border-gray-200 p-5 rounded-2xl space-y-2 shadow-sm">
          <div className="flex items-center justify-between text-xs text-gray-600 font-semibold">
            <span>People</span>
            <div className="p-2 rounded-lg bg-gray-100 text-[#088ADA] border border-gray-200">
              <Users className="h-4 w-4" />
            </div>
          </div>
          <div className="text-2xl font-bold font-mono text-gray-900">
            {summaryStats.active_users_count}
          </div>
          <p className="text-xs text-gray-500">People who asked the AI something</p>
        </div>
      </div>

      {/* Controls & Filter Bar */}
      <div className="bg-white p-4 rounded-2xl border border-gray-200 shadow-sm flex flex-col md:flex-row items-center justify-between gap-4">
        {/* Navigation Tabs */}
        <div className="flex bg-gray-100 p-1 rounded-xl border border-gray-200 text-xs font-semibold w-full md:w-auto flex-wrap">
          <button
            onClick={() => setActiveTab("channel")}
            className={`px-4 py-2 rounded-lg transition flex items-center gap-1.5 ${
              activeTab === "channel"
                ? "bg-[#088ADA] text-white shadow"
                : "text-gray-600 hover:text-gray-900"
            }`}
          >
            <FileText className="h-3.5 w-3.5" />
            <span>By channel</span>
          </button>
          <button
            onClick={() => setActiveTab("user")}
            className={`px-4 py-2 rounded-lg transition flex items-center gap-1.5 ${
              activeTab === "user"
                ? "bg-[#088ADA] text-white shadow"
                : "text-gray-600 hover:text-gray-900"
            }`}
          >
            <User className="h-3.5 w-3.5" />
            <span>By person</span>
          </button>
          <button
            onClick={() => setActiveTab("channel_user")}
            className={`px-4 py-2 rounded-lg transition flex items-center gap-1.5 ${
              activeTab === "channel_user"
                ? "bg-[#088ADA] text-white shadow"
                : "text-gray-600 hover:text-gray-900"
            }`}
          >
            <Users className="h-3.5 w-3.5" />
            <span>Person per channel</span>
          </button>
          <button
            onClick={() => setActiveTab("logs")}
            className={`px-4 py-2 rounded-lg transition flex items-center gap-1.5 ${
              activeTab === "logs"
                ? "bg-[#088ADA] text-white shadow"
                : "text-gray-600 hover:text-gray-900"
            }`}
          >
            <Zap className="h-3.5 w-3.5" />
            <span>Every reply</span>
          </button>
        </div>

        {/* Workspace Dropdown & Instant Search */}
        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3 w-full md:w-auto">
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
    </div>
  );
}

