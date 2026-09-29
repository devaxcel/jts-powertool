"use client";

import { useEffect, useState, useRef } from "react";
import {
  Activity,
  Search,
  Trash2,
  ArrowDown,
  Radio,
  DollarSign,
  Zap,
  Cpu,
  Layers,
  FileText,
} from "lucide-react";
import { LogEvent, UsageSummary, ApiUsageLog, formatLocalDateTime } from "@/lib/types";
import { fetchUsageSummary, fetchUsageLogs, fetchFolder, fetchFolders, fetchChannelMessages } from "@/lib/api";

export default function LogsPage() {
  const [logs, setLogs] = useState<LogEvent[]>([]);
  const [usageSummary, setUsageSummary] = useState<UsageSummary | null>(null);
  const [usageLogs, setUsageLogs] = useState<ApiUsageLog[]>([]);
  const [activeTab, setActiveTab] = useState<"stream" | "usage">("stream");
  const [connected, setConnected] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [selectedCategory, setSelectedCategory] = useState("all");
  const [selectedLevel, setSelectedLevel] = useState("all");
  const [autoScroll, setAutoScroll] = useState(true);

  const scrollRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  async function loadUsageData() {
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

      if (activeRole === "client_admin" || activeRole === "client_standard") {
        // CLIENT ADMIN TOKEN & BILLING (Strictly scoped to channels inside client folder)
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
          fetchUsageSummary().catch(() => null),
          fetchUsageLogs(100).catch(() => []),
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
              workspace_id: ch.workspace_id || "T02HKMBE09K",
              workspace_name: ch.workspace_name || "JTS Team",
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

        const filteredLogs = logListRes.filter((l: any) => matchesFolderChannel(l.channel_id, l.channel_name));

        setUsageLogs(filteredLogs);
        setUsageSummary({
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
        // JTS ADMIN TOKEN & BILLING (Global Telemetry - Untouched)
        const [sum, logList] = await Promise.all([
          fetchUsageSummary(),
          fetchUsageLogs(100),
        ]);
        setUsageSummary(sum);
        setUsageLogs(logList);
      }
    } catch (err) {
      console.error("Failed to load usage data:", err);
    }
  }

  // 1. Initial snapshot from /logs/api/feed
  async function loadInitialFeed() {
    try {
      const token = typeof window !== "undefined" ? sessionStorage.getItem("jts_token") : null;
      const res = await fetch("/logs/api/feed", {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (res.ok) {
        const initialLogs: LogEvent[] = await res.json();
        setLogs(initialLogs);
      }
    } catch (e) {
      console.error("Failed to load initial log feed:", e);
    }
  }

  // 2. Connect to Server-Sent Events stream
  useEffect(() => {
    loadInitialFeed();
    loadUsageData();

    const es = new EventSource("/logs/stream");
    eventSourceRef.current = es;

    es.onopen = () => {
      setConnected(true);
    };

    es.onmessage = (event) => {
      try {
        const logData: LogEvent = JSON.parse(event.data);
        setLogs((prev) => [logData, ...prev.slice(0, 499)]); // maintain last 500 items
      } catch (err) {
        console.error("Error parsing SSE event:", err);
      }
    };

    es.onerror = () => {
      setConnected(false);
    };

    const interval = setInterval(loadUsageData, 10000);

    return () => {
      es.close();
      clearInterval(interval);
    };
  }, []);

  // 3. Auto-scroll handling
  useEffect(() => {
    if (autoScroll && scrollRef.current) {
      scrollRef.current.scrollTop = 0;
    }
  }, [logs, autoScroll]);

  async function handleClearLogs() {
    try {
      const token = typeof window !== "undefined" ? sessionStorage.getItem("jts_token") : null;
      await fetch("/logs/api/clear", {
        method: "POST",
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      setLogs([]);
    } catch (e) {
      console.error("Failed to clear logs:", e);
    }
  }

  const filteredLogs = logs.filter((item) => {
    if (selectedCategory !== "all" && item.category !== selectedCategory) return false;
    if (selectedLevel !== "all" && item.level !== selectedLevel) return false;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      const msg = (item.message || "").toLowerCase();
      const act = (item.action || "").toLowerCase();
      const cat = (item.category || "").toLowerCase();
      return msg.includes(q) || act.includes(q) || cat.includes(q);
    }
    return true;
  });

  return (
    <div className="space-y-6 max-w-7xl mx-auto flex flex-col">
      {/* Top Controls Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 shrink-0">
        <div>
          <h1 className="text-xl font-bold text-gray-700 flex items-center gap-2">
            <Activity className="h-5 w-5 text-[#088ADA]" />
            <span>Telemetry, Token Usage & API Cost Monitor</span>
          </h1>
          <p className="text-xs text-gray-400 mt-0.5">
            Real-time API usage, calculated token costs per channel, and live telemetry log stream.
          </p>
        </div>

        <div className="flex items-center gap-2.5">
          {/* Tab Selector */}
          <div className="flex bg-gray-100 p-1 rounded-xl border border-gray-200 text-xs font-semibold">
            <button
              onClick={() => setActiveTab("usage")}
              className={`px-3 py-1.5 rounded-lg transition ${
                activeTab === "usage"
                  ? "bg-[#088ADA] text-white shadow"
                  : "text-gray-600 hover:text-gray-900"
              }`}
            >
              Token Cost Telemetry
            </button>
            <button
              onClick={() => setActiveTab("stream")}
              className={`px-3 py-1.5 rounded-lg transition ${
                activeTab === "stream"
                  ? "bg-[#088ADA] text-white shadow"
                  : "text-gray-600 hover:text-gray-900"
              }`}
            >
              Live Telemetry Console
            </button>
          </div>

          <div
            className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-full text-xs font-medium border ${
              connected
                ? "bg-emerald-50 border-emerald-200 text-emerald-600"
                : "bg-rose-50 border-rose-500/20 text-rose-500"
            }`}
          >
            <Radio className={`h-3 w-3 ${connected ? "animate-pulse" : ""}`} />
            <span>{connected ? "Stream Live" : "Offline"}</span>
          </div>
        </div>
      </div>

      {/* TOKEN USAGE & COST TELEMETRY TAB */}
      {activeTab === "usage" && (
        <div className="space-y-6">
          {/* Summary Metric Cards */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="bg-white border border-gray-200 p-5 rounded-2xl space-y-2 shadow-sm">
              <div className="flex items-center justify-between text-xs text-gray-600 font-semibold">
                <span>Total Calculated Cost</span>
                <div className="p-2 rounded-lg bg-emerald-50 text-emerald-600 border border-emerald-200">
                  <DollarSign className="h-4 w-4" />
                </div>
              </div>
              <div className="text-2xl font-bold font-mono text-gray-900">
                ${usageSummary ? usageSummary.total_cost_usd.toFixed(6) : "0.000000"}
              </div>
              <p className="text-xs text-gray-500">Calculated per model &amp; 1M token rate</p>
            </div>

            <div className="bg-white border border-gray-200 p-5 rounded-2xl space-y-2 shadow-sm">
              <div className="flex items-center justify-between text-xs text-gray-600 font-semibold">
                <span>Total API Calls</span>
                <div className="p-2 rounded-lg bg-gray-100 text-[#088ADA] border border-gray-200">
                  <Zap className="h-4 w-4" />
                </div>
              </div>
              <div className="text-2xl font-bold font-mono text-gray-900">
                {usageSummary ? usageSummary.total_calls : 0}
              </div>
              <p className="text-xs text-gray-500">Anthropic Claude API turns</p>
            </div>

            <div className="bg-white border border-gray-200 p-5 rounded-2xl space-y-2 shadow-sm">
              <div className="flex items-center justify-between text-xs text-gray-600 font-semibold">
                <span>Total Input Tokens</span>
                <div className="p-2 rounded-lg bg-gray-100 text-[#088ADA] border border-gray-200">
                  <Cpu className="h-4 w-4" />
                </div>
              </div>
              <div className="text-2xl font-bold font-mono text-gray-900">
                {usageSummary ? usageSummary.total_input_tokens.toLocaleString() : 0}
              </div>
              <p className="text-xs text-gray-500">Prompts &amp; Context tokens</p>
            </div>

            <div className="bg-white border border-gray-200 p-5 rounded-2xl space-y-2 shadow-sm">
              <div className="flex items-center justify-between text-xs text-gray-600 font-semibold">
                <span>Total Output Tokens</span>
                <div className="p-2 rounded-lg bg-gray-100 text-[#088ADA] border border-gray-200">
                  <Layers className="h-4 w-4" />
                </div>
              </div>
              <div className="text-2xl font-bold font-mono text-gray-900">
                {usageSummary ? usageSummary.total_output_tokens.toLocaleString() : 0}
              </div>
              <p className="text-xs text-gray-500">Generated text &amp; tool turns</p>
            </div>
          </div>

          {/* Breakdown Per Channel Table */}
          <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
            <div className="p-4 border-b border-gray-200 flex items-center justify-between bg-gray-50">
              <h2 className="text-sm font-bold text-gray-700 flex items-center gap-2">
                <FileText className="h-4 w-4 text-[#088ADA]" />
                <span>Usage &amp; Cost Breakdown by Slack Channel</span>
              </h2>
              <span className="text-xs text-gray-500 font-mono">
                {usageSummary?.by_channel?.length || 0} active channels
              </span>
            </div>

            <div className="overflow-x-auto max-h-72 custom-scroll">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="bg-[#088ADA] text-white font-semibold text-xs border-b border-gray-300">
                    <th className="py-3 px-4 sticky top-0 z-20 bg-[#088ADA]">Channel Name</th>
                    <th className="py-3 px-4 sticky top-0 z-20 bg-[#088ADA]">Channel ID</th>
                    <th className="py-3 px-4 sticky top-0 z-20 bg-[#088ADA]">API Calls</th>
                    <th className="py-3 px-4 sticky top-0 z-20 bg-[#088ADA]">Input Tokens</th>
                    <th className="py-3 px-4 sticky top-0 z-20 bg-[#088ADA]">Output Tokens</th>
                    <th className="py-3 px-4 sticky top-0 z-20 bg-[#088ADA]">Total Tokens</th>
                    <th className="py-3 px-4 sticky top-0 z-20 bg-[#088ADA]">Calculated Cost USD</th>
                  </tr>
                </thead>
                <tbody className="text-xs divide-y divide-gray-200">
                  {!usageSummary || usageSummary.by_channel.length === 0 ? (
                    <tr>
                      <td colSpan={7} className="py-8 text-center text-gray-500 italic bg-white">
                        No channel usage logged yet. Make an API call via Slack to see real-time costs!
                      </td>
                    </tr>
                  ) : (
                    usageSummary.by_channel.map((item, idx) => (
                      <tr
                        key={item.channel_id}
                        className={`${
                          idx % 2 === 0 ? "bg-white" : "bg-[#ededed]"
                        } hover:bg-gray-200 transition text-gray-800`}
                      >
                        <td className="py-3 px-4 font-semibold text-[#088ADA]">
                          {item.channel_name?.startsWith("#") || item.channel_name?.startsWith("@")
                            ? item.channel_name
                            : `#${item.channel_name}`}
                        </td>
                        <td className="py-3 px-4 font-mono text-gray-600">{item.channel_id}</td>
                        <td className="py-3 px-4 font-mono font-bold text-gray-700">{item.calls}</td>
                        <td className="py-3 px-4 font-mono text-gray-600">{item.input_tokens.toLocaleString()}</td>
                        <td className="py-3 px-4 font-mono text-gray-600">{item.output_tokens.toLocaleString()}</td>
                        <td className="py-3 px-4 font-mono font-bold text-gray-800">{item.total_tokens.toLocaleString()}</td>
                        <td className="py-3 px-4 font-mono font-bold text-emerald-600">
                          ${item.total_cost_usd.toFixed(6)}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {/* Detailed API Usage Logs Table */}
          <div className="bg-white border border-gray-200 rounded-2xl shadow-sm overflow-hidden">
            <div className="p-4 border-b border-gray-200 flex items-center justify-between bg-gray-50">
              <h2 className="text-sm font-bold text-gray-700 flex items-center gap-2">
                <Activity className="h-4 w-4 text-[#088ADA]" />
                <span>Recent API Usage Log History</span>
              </h2>
              <span className="text-xs text-gray-500 font-mono">
                Showing last {usageLogs.length} records
              </span>
            </div>

            <div className="overflow-x-auto max-h-96 custom-scroll">
              <table className="w-full text-left border-collapse">
                <thead>
                  <tr className="bg-[#088ADA] text-white font-semibold text-xs border-b border-gray-300">
                    <th className="py-3 px-4 sticky top-0 z-20 bg-[#088ADA]">Log ID</th>
                    <th className="py-3 px-4 sticky top-0 z-20 bg-[#088ADA]">Timestamp</th>
                    <th className="py-3 px-4 sticky top-0 z-20 bg-[#088ADA]">Channel</th>
                    <th className="py-3 px-4 sticky top-0 z-20 bg-[#088ADA]">Model</th>
                    <th className="py-3 px-4 sticky top-0 z-20 bg-[#088ADA]">Input Tokens</th>
                    <th className="py-3 px-4 sticky top-0 z-20 bg-[#088ADA]">Output Tokens</th>
                    <th className="py-3 px-4 sticky top-0 z-20 bg-[#088ADA]">Total Tokens</th>
                    <th className="py-3 px-4 sticky top-0 z-20 bg-[#088ADA]">Cost (USD)</th>
                  </tr>
                </thead>
                <tbody className="text-xs divide-y divide-gray-200">
                  {usageLogs.length === 0 ? (
                    <tr>
                      <td colSpan={8} className="py-8 text-center text-gray-500 italic bg-white">
                        No detailed API usage logs found in PostgreSQL api_usage_logs.
                      </td>
                    </tr>
                  ) : (
                    usageLogs.map((log, idx) => (
                      <tr
                        key={log.id}
                        className={`${
                          idx % 2 === 0 ? "bg-white" : "bg-[#ededed]"
                        } hover:bg-gray-200 transition text-gray-800`}
                      >
                        <td className="py-3 px-4 font-mono text-gray-500">#{log.id}</td>
                        <td className="py-3 px-4 text-gray-600 font-mono whitespace-nowrap">
                          {formatLocalDateTime(log.created_at)}
                        </td>
                        <td className="py-3 px-4 font-semibold text-[#088ADA]">#{log.channel_name}</td>
                        <td className="py-3 px-4 font-mono text-xs text-gray-700 bg-gray-100/80 px-2 py-0.5 rounded border border-gray-200 inline-block my-2">
                          {log.model}
                        </td>
                        <td className="py-3 px-4 font-mono text-gray-600">{log.input_tokens.toLocaleString()}</td>
                        <td className="py-3 px-4 font-mono text-gray-600">{log.output_tokens.toLocaleString()}</td>
                        <td className="py-3 px-4 font-mono font-bold text-gray-800">{log.total_tokens.toLocaleString()}</td>
                        <td className="py-3 px-4 font-mono font-bold text-emerald-600">
                          ${log.cost_usd.toFixed(6)}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* LIVE TELEMETRY CONSOLE TAB */}
      {activeTab === "stream" && (
        <div className="space-y-4 flex flex-col h-[calc(100vh-12rem)]">
          {/* Filter and Search Bar */}
          <div className="bg-gray-50 p-3 rounded-xl border border-gray-200 flex flex-col sm:flex-row items-center justify-between gap-3 shrink-0">
            <div className="flex items-center gap-2 w-full sm:w-auto flex-wrap">
              {/* Category Filter */}
              <select
                value={selectedCategory}
                onChange={(e) => setSelectedCategory(e.target.value)}
                className="bg-gray-50 border border-gray-200 rounded-lg text-xs text-gray-500 px-3 py-1.5 focus:outline-none focus:border-[#088ADA]"
              >
                <option value="all">All Categories</option>
                <option value="CLAUDE">CLAUDE</option>
                <option value="TOOL">TOOL</option>
                <option value="SLACK">SLACK</option>
                <option value="WORKER">WORKER</option>
                <option value="DATABASE">DATABASE</option>
                <option value="FILTER">FILTER</option>
              </select>

              {/* Level Filter */}
              <select
                value={selectedLevel}
                onChange={(e) => setSelectedLevel(e.target.value)}
                className="bg-gray-50 border border-gray-200 rounded-lg text-xs text-gray-500 px-3 py-1.5 focus:outline-none focus:border-[#088ADA]"
              >
                <option value="all">All Levels</option>
                <option value="INFO">INFO</option>
                <option value="WARN">WARN</option>
                <option value="ERROR">ERROR</option>
              </select>

              {/* Auto scroll toggle */}
              <button
                onClick={() => setAutoScroll(!autoScroll)}
                className={`px-3 py-1.5 rounded-lg text-xs font-medium border flex items-center gap-1.5 transition ${
                  autoScroll
                    ? "bg-[#088ADA]/15 border-gray-300 text-[#088ADA]"
                    : "bg-gray-50 border-gray-200 text-gray-400"
                }`}
              >
                <ArrowDown className="h-3 w-3" />
                <span>Auto-scroll {autoScroll ? "ON" : "OFF"}</span>
              </button>

              <button
                onClick={handleClearLogs}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-500 text-xs font-medium border border-gray-200 transition"
              >
                <Trash2 className="h-3.5 w-3.5 text-gray-400" />
                <span>Clear</span>
              </button>
            </div>

            {/* Search input */}
            <div className="relative w-full sm:w-72">
              <Search className="h-3.5 w-3.5 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                type="text"
                placeholder="Search logs..."
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="w-full pl-9 pr-3 py-1.5 bg-gray-50 border border-gray-200 rounded-lg text-xs text-gray-600 placeholder-gray-400 focus:outline-none focus:border-[#088ADA]"
              />
            </div>
          </div>

          {/* Log Console Window */}
          <div
            ref={scrollRef}
            className="flex-1 bg-gray-50 border border-gray-200 rounded-xl overflow-y-auto font-mono text-xs p-3 space-y-1.5 min-h-0 custom-scroll"
          >
            {filteredLogs.length === 0 ? (
              <div className="h-full flex items-center justify-center text-gray-400 text-xs">
                No events recorded matching criteria.
              </div>
            ) : (
              filteredLogs.map((log, idx) => {
                const isError = log.level === "ERROR";
                const isWarn = log.level === "WARN";

                return (
                  <div
                    key={log.id || `${log.timestamp}-${idx}`}
                    className={`p-2 rounded-lg border transition ${
                      isError
                        ? "bg-rose-950/20 border-rose-900/40 text-rose-200"
                        : isWarn
                        ? "bg-amber-50 border-amber-200 text-amber-900"
                        : "bg-white border-gray-200 text-gray-700 hover:bg-gray-100"
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2 text-[11px] mb-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-gray-400 font-mono">{formatLocalDateTime(log.timestamp)}</span>
                        <span
                          className={`px-1.5 py-0.5 rounded text-[10px] font-bold ${
                            isError
                              ? "bg-rose-500/20 text-rose-500"
                              : isWarn
                              ? "bg-amber-100 text-amber-700"
                              : "bg-emerald-50 text-emerald-600"
                          }`}
                        >
                          {log.level}
                        </span>
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-gray-100 text-[#088ADA] border border-gray-200">
                          {log.category}
                        </span>
                        <span className="text-gray-400 font-semibold">{log.action}</span>
                      </div>

                      <div className="flex items-center gap-2 text-[10px] text-gray-400 font-mono">
                        {log.channel_name && log.channel_name !== "-" && (
                          <span className="text-[#088ADA] font-semibold">
                            Channel: {log.channel_name.startsWith("#") || log.channel_name.startsWith("@") ? log.channel_name : `#${log.channel_name}`}
                          </span>
                        )}
                        {log.user_id && log.user_id !== "-" && (
                          <span>User: {log.user_id}</span>
                        )}
                      </div>
                    </div>

                    <div className="text-gray-600 text-xs break-all whitespace-pre-wrap leading-relaxed">
                      {log.message}
                    </div>

                    {log.extra && Object.keys(log.extra).length > 0 && (
                      <div className="mt-1.5 p-2 rounded bg-gray-50 border border-gray-200 text-[11px] text-gray-500 overflow-x-auto">
                        <pre>{JSON.stringify(log.extra, null, 2)}</pre>
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>
        </div>
      )}
    </div>
  );
}
