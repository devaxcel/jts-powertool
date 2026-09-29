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
import { UsageSummary, ApiUsageLog, formatLocalDateTime } from "@/lib/types";
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

                if (isBot) {
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
      {/* Top Header Banner */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 bg-[#088ADA] border border-gray-300 p-6 rounded-2xl shadow-md text-white">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Receipt className="h-6 w-6 text-white" />
            <h1 className="text-xl font-bold text-white tracking-tight">
              Token Usage &amp; Billing Control Center
            </h1>
            <span className="text-xs bg-white/20 text-white px-2.5 py-0.5 rounded-full font-mono border border-white/30">
              Live Billed Rates
            </span>
          </div>
          <p className="text-xs sm:text-sm text-gray-200">
            Track real-time token consumption, calculated API cost USD, and granular billing breakdowns for every Slack workspace, channel, and user.
          </p>
        </div>

        {/* Action Buttons Aligned Cleanly Side-by-Side */}
        <div className="flex flex-row items-center gap-2.5 self-start md:self-auto shrink-0">
          <button
            onClick={loadBillingData}
            disabled={loading || clearing}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-white hover:bg-gray-100 text-gray-800 text-xs font-semibold shadow-md transition disabled:opacity-50"
          >
            <RefreshCw className={`h-3.5 w-3.5 text-[#088ADA] ${loading ? "animate-spin" : ""}`} />
            <span>Refresh Telemetry</span>
          </button>

          {isMasterAdmin && (
            <button
              onClick={handleClearBillingData}
              disabled={loading || clearing}
              className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-red-600 hover:bg-red-700 text-white text-xs font-semibold shadow-md transition disabled:opacity-50"
            >
              <Trash2 className={`h-3.5 w-3.5 ${clearing ? "animate-spin" : ""}`} />
              <span>Clear Billing Data</span>
            </button>
          )}
        </div>
      </div>

      {/* Summary KPI Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Total Cost */}
        <div className="bg-white border border-gray-200 p-5 rounded-2xl space-y-2 shadow-sm">
          <div className="flex items-center justify-between text-xs text-gray-600 font-semibold">
            <span>Total Billed Cost</span>
            <div className="p-2 rounded-lg bg-emerald-50 text-emerald-600 border border-emerald-200">
              <DollarSign className="h-4 w-4" />
            </div>
          </div>
          <div className="text-2xl font-bold font-mono text-gray-900">
            ${summaryStats.total_cost_usd.toFixed(6)}
          </div>
          <p className="text-xs text-gray-500">Calculated per model rate &amp; 1M tokens</p>
        </div>

        {/* Total Tokens */}
        <div className="bg-white border border-gray-200 p-5 rounded-2xl space-y-2 shadow-sm">
          <div className="flex items-center justify-between text-xs text-gray-600 font-semibold">
            <span>Total Tokens Billed</span>
            <div className="p-2 rounded-lg bg-gray-100 text-[#088ADA] border border-gray-200">
              <Cpu className="h-4 w-4" />
            </div>
          </div>
          <div className="text-2xl font-bold font-mono text-gray-900">
            {summaryStats.total_tokens.toLocaleString()}
          </div>
          <p className="text-xs text-gray-500">
            {`${summaryStats.total_input_tokens.toLocaleString()} in / ${summaryStats.total_output_tokens.toLocaleString()} out`}
          </p>
        </div>

        {/* Active Channels */}
        <div className="bg-white border border-gray-200 p-5 rounded-2xl space-y-2 shadow-sm">
          <div className="flex items-center justify-between text-xs text-gray-600 font-semibold">
            <span>Billed Channels</span>
            <div className="p-2 rounded-lg bg-gray-100 text-[#088ADA] border border-gray-200">
              <Layers className="h-4 w-4" />
            </div>
          </div>
          <div className="text-2xl font-bold font-mono text-gray-900">
            {summaryStats.active_channels_count}
          </div>
          <p className="text-xs text-gray-500">Channels with active API calls</p>
        </div>

        {/* Active Users */}
        <div className="bg-white border border-gray-200 p-5 rounded-2xl space-y-2 shadow-sm">
          <div className="flex items-center justify-between text-xs text-gray-600 font-semibold">
            <span>Billed Users</span>
            <div className="p-2 rounded-lg bg-gray-100 text-[#088ADA] border border-gray-200">
              <Users className="h-4 w-4" />
            </div>
          </div>
          <div className="text-2xl font-bold font-mono text-gray-900">
            {summaryStats.active_users_count}
          </div>
          <p className="text-xs text-gray-500">Unique user IDs generating calls</p>
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
            <span>Billing by Channel</span>
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
            <span>Billing by User</span>
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
            <span>User Breakdown in Channel</span>
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
            <span>Call History Logs</span>
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
                <option value="ALL">🌐 All Workspaces</option>
                {availableWorkspaces.map((ws) => {
                  const displayName = ws.workspace_name.includes(ws.workspace_id)
                    ? ws.workspace_name
                    : `${ws.workspace_name} (${ws.workspace_id})`;
                  return (
                    <option key={ws.workspace_id} value={ws.workspace_id}>
                      🏢 {displayName}
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
              placeholder="Search channel or user ID..."
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
          <div className="p-4 border-b border-gray-200 bg-gray-50 flex items-center justify-between">
            <h2 className="text-sm font-bold text-gray-800 flex items-center gap-2">
              <FileText className="h-4 w-4 text-[#088ADA]" />
              <span>Token Usage &amp; Billed Costs Grouped by Channel</span>
            </h2>
            <span className="text-xs font-mono text-gray-500">
              {filteredChannels.length} channels displayed
            </span>
          </div>

          <div className="overflow-x-auto max-h-[500px] custom-scroll">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-[#088ADA] text-white font-semibold text-xs border-b border-gray-300">
                  {isMasterAdmin && <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Workspace ID</th>}
                  {isMasterAdmin && <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Slack Workspace</th>}
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Channel Name</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Channel ID</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Total API Calls</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Input Tokens</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Output Tokens</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Total Tokens</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Billed Cost (USD)</th>
                  {isMasterAdmin && <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA] text-right">Actions</th>}
                </tr>
              </thead>
              <tbody className="text-xs divide-y divide-gray-200">
                {filteredChannels.length === 0 ? (
                  <tr>
                    <td colSpan={isMasterAdmin ? 10 : 7} className="py-8 text-center text-gray-500 italic bg-white">
                      No channel usage records match the filter criteria.
                    </td>
                  </tr>
                ) : (
                  filteredChannels.map((item, idx) => (
                    <tr
                      key={`${item.workspace_id}-${item.channel_id}-${idx}`}
                      className={`${
                        idx % 2 === 0 ? "bg-white" : "bg-[#ededed]"
                      } hover:bg-gray-200 transition text-gray-800`}
                    >
                      {isMasterAdmin && (
                        <td className="py-3.5 px-4 font-mono">
                          <span className="px-2 py-0.5 rounded bg-blue-100 text-blue-800 font-mono font-bold text-[10px] border border-blue-200">
                            {item.workspace_id || "T01..."}
                          </span>
                        </td>
                      )}
                      {isMasterAdmin && (
                        <td className="py-3.5 px-4 font-bold text-gray-900">{item.workspace_name || "Axcel World"}</td>
                      )}
                      <td className="py-3.5 px-4 font-semibold text-[#088ADA]">{formatChannelName(item.channel_name)}</td>
                      <td className="py-3.5 px-4 font-mono text-gray-600">{item.channel_id}</td>
                      <td className="py-3.5 px-4 font-mono font-bold text-gray-700">{item.calls}</td>
                      <td className="py-3.5 px-4 font-mono text-gray-600">{item.input_tokens.toLocaleString()}</td>
                      <td className="py-3.5 px-4 font-mono text-gray-600">{item.output_tokens.toLocaleString()}</td>
                      <td className="py-3.5 px-4 font-mono font-bold text-gray-800">{item.total_tokens.toLocaleString()}</td>
                      <td className="py-3.5 px-4 font-mono font-bold text-emerald-600">
                        ${item.total_cost_usd.toFixed(6)}
                      </td>
                      {isMasterAdmin && (
                        <td className="py-3.5 px-4 text-right">
                          <button
                            onClick={() => handleDeleteChannel(item.channel_id, item.channel_name)}
                            disabled={deletingId === `chan-${item.channel_id}`}
                            className="px-2.5 py-1 bg-red-100 hover:bg-red-200 text-red-700 rounded-lg text-xs font-semibold transition border border-red-200 inline-flex items-center gap-1 disabled:opacity-50"
                            title="Delete all billing records for this channel"
                          >
                            <Trash2 className="h-3 w-3" />
                            <span>Delete</span>
                          </button>
                        </td>
                      )}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 2. BILLING BY USER TABLE */}
      {activeTab === "user" && (
        <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
          <div className="p-4 border-b border-gray-200 bg-gray-50 flex items-center justify-between">
            <h2 className="text-sm font-bold text-gray-800 flex items-center gap-2">
              <User className="h-4 w-4 text-[#088ADA]" />
              <span>Token Usage &amp; Billed Costs Grouped by User</span>
            </h2>
            <span className="text-xs font-mono text-gray-500">
              {filteredUsers.length} users displayed
            </span>
          </div>

          <div className="overflow-x-auto max-h-[500px] custom-scroll">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-[#088ADA] text-white font-semibold text-xs border-b border-gray-300">
                  {isMasterAdmin && <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Workspace ID</th>}
                  {isMasterAdmin && <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Slack Workspace</th>}
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">User Name</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">User ID</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Total API Calls</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Input Tokens</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Output Tokens</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Total Tokens</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Billed Cost (USD)</th>
                  {isMasterAdmin && <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA] text-right">Actions</th>}
                </tr>
              </thead>
              <tbody className="text-xs divide-y divide-gray-200">
                {filteredUsers.length === 0 ? (
                  <tr>
                    <td colSpan={isMasterAdmin ? 10 : 7} className="py-8 text-center text-gray-500 italic bg-white">
                      No user usage records found.
                    </td>
                  </tr>
                ) : (
                  filteredUsers.map((item, idx) => (
                    <tr
                      key={`${item.workspace_id}-${item.user_id}-${idx}`}
                      className={`${
                        idx % 2 === 0 ? "bg-white" : "bg-[#ededed]"
                      } hover:bg-gray-200 transition text-gray-800`}
                    >
                      {isMasterAdmin && (
                        <td className="py-3.5 px-4 font-mono">
                          <span className="px-2 py-0.5 rounded bg-blue-100 text-blue-800 font-mono font-bold text-[10px] border border-blue-200">
                            {item.workspace_id || "T01..."}
                          </span>
                        </td>
                      )}
                      {isMasterAdmin && (
                        <td className="py-3.5 px-4 font-bold text-gray-900">{item.workspace_name || "Axcel World"}</td>
                      )}
                      <td className="py-3.5 px-4 font-semibold text-gray-800">
                        {item.user_name || item.user_id}
                      </td>
                      <td className="py-3.5 px-4 font-mono text-gray-600">
                        {item.user_id}
                      </td>
                      <td className="py-3.5 px-4 font-mono font-bold text-gray-700">{item.calls}</td>
                      <td className="py-3.5 px-4 font-mono text-gray-600">{item.input_tokens.toLocaleString()}</td>
                      <td className="py-3.5 px-4 font-mono text-gray-600">{item.output_tokens.toLocaleString()}</td>
                      <td className="py-3.5 px-4 font-mono font-bold text-gray-800">{item.total_tokens.toLocaleString()}</td>
                      <td className="py-3.5 px-4 font-mono font-bold text-emerald-600">
                        ${item.total_cost_usd.toFixed(6)}
                      </td>
                      {isMasterAdmin && (
                        <td className="py-3.5 px-4 text-right">
                          <button
                            onClick={() => handleDeleteUser(item.user_id, item.user_name || item.user_id)}
                            disabled={deletingId === `user-${item.user_id}`}
                            className="px-2.5 py-1 bg-red-100 hover:bg-red-200 text-red-700 rounded-lg text-xs font-semibold transition border border-red-200 inline-flex items-center gap-1 disabled:opacity-50"
                            title="Delete all billing records for this user"
                          >
                            <Trash2 className="h-3 w-3" />
                            <span>Delete</span>
                          </button>
                        </td>
                      )}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 3. USER BREAKDOWN PER CHANNEL TABLE */}
      {activeTab === "channel_user" && (
        <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
          <div className="p-4 border-b border-gray-200 bg-gray-50 flex items-center justify-between">
            <h2 className="text-sm font-bold text-gray-800 flex items-center gap-2">
              <Users className="h-4 w-4 text-[#088ADA]" />
              <span>Granular User Billing Breakdown Within Each Channel</span>
            </h2>
            <span className="text-xs font-mono text-gray-500">
              {filteredChannelUsers.length} user-in-channel entries
            </span>
          </div>

          <div className="overflow-x-auto max-h-[500px] custom-scroll">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-[#088ADA] text-white font-semibold text-xs border-b border-gray-300">
                  {isMasterAdmin && <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Workspace ID</th>}
                  {isMasterAdmin && <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Slack Workspace</th>}
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Channel Name</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Channel ID</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">User Name</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">User ID</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">API Calls</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Input Tokens</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Output Tokens</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Total Tokens</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Billed Cost (USD)</th>
                  {isMasterAdmin && <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA] text-right">Actions</th>}
                </tr>
              </thead>
              <tbody className="text-xs divide-y divide-gray-200">
                {filteredChannelUsers.length === 0 ? (
                  <tr>
                    <td colSpan={isMasterAdmin ? 12 : 9} className="py-8 text-center text-gray-500 italic bg-white">
                      No user breakdown records match the filter criteria.
                    </td>
                  </tr>
                ) : (
                  filteredChannelUsers.map((item, idx) => (
                    <tr
                      key={`${item.workspace_id}-${item.channel_id}-${item.user_id}-${idx}`}
                      className={`${
                        idx % 2 === 0 ? "bg-white" : "bg-[#ededed]"
                      } hover:bg-gray-200 transition text-gray-800`}
                    >
                      {isMasterAdmin && (
                        <td className="py-3.5 px-4 font-mono">
                          <span className="px-2 py-0.5 rounded bg-blue-100 text-blue-800 font-mono font-bold text-[10px] border border-blue-200">
                            {item.workspace_id || "T01..."}
                          </span>
                        </td>
                      )}
                      {isMasterAdmin && (
                        <td className="py-3.5 px-4 font-bold text-gray-900">{item.workspace_name || "Axcel World"}</td>
                      )}
                      <td className="py-3.5 px-4 font-semibold text-[#088ADA]">{formatChannelName(item.channel_name)}</td>
                      <td className="py-3.5 px-4 font-mono text-gray-600">{item.channel_id}</td>
                      <td className="py-3.5 px-4 font-semibold text-gray-800">{item.user_name || item.user_id}</td>
                      <td className="py-3.5 px-4 font-mono text-gray-600">{item.user_id}</td>
                      <td className="py-3.5 px-4 font-mono font-bold text-gray-700">{item.calls}</td>
                      <td className="py-3.5 px-4 font-mono text-gray-600">{item.input_tokens.toLocaleString()}</td>
                      <td className="py-3.5 px-4 font-mono text-gray-600">{item.output_tokens.toLocaleString()}</td>
                      <td className="py-3.5 px-4 font-mono font-bold text-gray-800">{item.total_tokens.toLocaleString()}</td>
                      <td className="py-3.5 px-4 font-mono font-bold text-emerald-600">
                        ${item.total_cost_usd.toFixed(6)}
                      </td>
                      {isMasterAdmin && (
                        <td className="py-3.5 px-4 text-right">
                          <button
                            onClick={() => handleDeleteChannelUser(item.channel_id, item.channel_name, item.user_id, item.user_name || item.user_id)}
                            disabled={deletingId === `chanuser-${item.channel_id}-${item.user_id}`}
                            className="px-2.5 py-1 bg-red-100 hover:bg-red-200 text-red-700 rounded-lg text-xs font-semibold transition border border-red-200 inline-flex items-center gap-1 disabled:opacity-50"
                            title="Delete records for this user in this channel"
                          >
                            <Trash2 className="h-3 w-3" />
                            <span>Delete Entry</span>
                          </button>
                        </td>
                      )}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 4. CALL HISTORY LOGS TABLE */}
      {activeTab === "logs" && (
        <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
          <div className="p-4 border-b border-gray-200 bg-gray-50 flex items-center justify-between">
            <h2 className="text-sm font-bold text-gray-800 flex items-center gap-2">
              <Zap className="h-4 w-4 text-[#088ADA]" />
              <span>Detailed Billed API Call History</span>
            </h2>
            <span className="text-xs font-mono text-gray-500">
              Showing last {filteredLogs.length} call logs
            </span>
          </div>

          <div className="overflow-x-auto max-h-[500px] custom-scroll">
            <table className="w-full text-left border-collapse">
              <thead>
                <tr className="bg-[#088ADA] text-white font-semibold text-xs border-b border-gray-300">
                  {isMasterAdmin && <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Workspace ID</th>}
                  {isMasterAdmin && <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Slack Workspace</th>}
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Call ID</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Timestamp</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Channel</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">User Name</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">User ID</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Model</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Input Tokens</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Output Tokens</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Total Tokens</th>
                  <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA]">Cost (USD)</th>
                  {isMasterAdmin && <th className="py-3.5 px-4 sticky top-0 z-20 bg-[#088ADA] text-right">Actions</th>}
                </tr>
              </thead>
              <tbody className="text-xs divide-y divide-gray-200">
                {filteredLogs.length === 0 ? (
                  <tr>
                    <td colSpan={isMasterAdmin ? 13 : 10} className="py-8 text-center text-gray-500 italic bg-white">
                      No API call logs recorded yet.
                    </td>
                  </tr>
                ) : (
                  filteredLogs.map((log, idx) => (
                    <tr
                      key={log.id}
                      className={`${
                        idx % 2 === 0 ? "bg-white" : "bg-[#ededed]"
                      } hover:bg-gray-200 transition text-gray-800`}
                    >
                      {isMasterAdmin && (
                        <td className="py-3.5 px-4 font-mono">
                          <span className="px-2 py-0.5 rounded bg-blue-100 text-blue-800 font-mono font-bold text-[10px] border border-blue-200">
                            {log.workspace_id || "T01..."}
                          </span>
                        </td>
                      )}
                      {isMasterAdmin && (
                        <td className="py-3.5 px-4 font-bold text-gray-900">{log.workspace_name || "Axcel World"}</td>
                      )}
                      <td className="py-3.5 px-4 font-mono text-gray-500">#{log.id}</td>
                      <td className="py-3.5 px-4 text-gray-600 font-mono whitespace-nowrap">
                        {formatLocalDateTime(log.created_at)}
                      </td>
                      <td className="py-3.5 px-4 font-semibold text-[#088ADA]">{formatChannelName(log.channel_name)}</td>
                      <td className="py-3.5 px-4 font-semibold text-gray-800">{log.user_name || log.user_id || "unknown"}</td>
                      <td className="py-3.5 px-4 font-mono text-gray-600">{log.user_id || "-"}</td>
                      <td className="py-3.5 px-4 font-mono text-xs text-gray-700 bg-gray-100 px-2 py-0.5 rounded border border-gray-200 inline-block my-1">
                        {log.model}
                      </td>
                      <td className="py-3.5 px-4 font-mono text-gray-600">{log.input_tokens.toLocaleString()}</td>
                      <td className="py-3.5 px-4 font-mono text-gray-600">{log.output_tokens.toLocaleString()}</td>
                      <td className="py-3.5 px-4 font-mono font-bold text-gray-800">{log.total_tokens.toLocaleString()}</td>
                      <td className="py-3.5 px-4 font-mono font-bold text-emerald-600">
                        ${log.cost_usd.toFixed(6)}
                      </td>
                      {isMasterAdmin && (
                        <td className="py-3.5 px-4 text-right">
                          <button
                            onClick={() => handleDeleteLog(log.id)}
                            disabled={deletingId === `log-${log.id}`}
                            className="px-2 py-1 bg-red-100 hover:bg-red-200 text-red-700 rounded-lg text-xs font-semibold transition border border-red-200 inline-flex items-center gap-1 disabled:opacity-50"
                            title="Delete this call log entry from database"
                          >
                            <Trash2 className="h-3 w-3" />
                            <span>Delete</span>
                          </button>
                        </td>
                      )}
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}

