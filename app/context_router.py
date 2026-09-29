import json
import logging
from datetime import datetime, date
from decimal import Decimal
from uuid import UUID
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from app.db.repositories import (
    get_latest_context_snapshot,
    get_context_snapshot_history,
    get_context_snapshot_by_id,
)

logger = logging.getLogger(__name__)
context_router = APIRouter(prefix="/context", tags=["Claude Context Inspector"])


def serialize_data(val):
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    if isinstance(val, UUID):
        return str(val)
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, dict):
        return {k: serialize_data(v) for k, v in val.items()}
    if isinstance(val, list):
        return [serialize_data(i) for i in val]
    return val


from fastapi import Request
from app.auth_router import get_user_context, get_folder_channel_ids


@context_router.get("/api/latest")
async def api_latest_snapshot(request: Request):
    """Retrieve the most recent Claude context snapshot."""
    user_ctx = get_user_context(request)
    if user_ctx.get("role") in ("client_admin", "client_standard") and user_ctx.get("client_folder_id"):
        folder_channels = set(get_folder_channel_ids(user_ctx["client_folder_id"]))
        history = get_context_snapshot_history(limit=50)
        history = [h for h in history if h.get("channel_id") in folder_channels]
        snapshot = history[0] if history else None
    else:
        snapshot = get_latest_context_snapshot()

    if not snapshot:
        return JSONResponse(content={"snapshot": None, "message": "No context snapshots recorded yet."})
    return JSONResponse(content={"snapshot": serialize_data(snapshot)})


@context_router.get("/api/history")
async def api_snapshot_history(request: Request, limit: int = 25):
    """Retrieve list of recent Claude context snapshots."""
    user_ctx = get_user_context(request)
    history = get_context_snapshot_history(limit=limit)

    if user_ctx.get("role") in ("client_admin", "client_standard") and user_ctx.get("client_folder_id"):
        folder_channels = set(get_folder_channel_ids(user_ctx["client_folder_id"]))
        history = [h for h in history if h.get("channel_id") in folder_channels]

    return JSONResponse(content={"history": serialize_data(history)})


@context_router.get("/api/snapshot/{snapshot_id}")
async def api_snapshot_by_id(snapshot_id: int, request: Request):
    """Retrieve a specific context snapshot by ID."""
    user_ctx = get_user_context(request)
    snapshot = get_context_snapshot_by_id(snapshot_id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Snapshot not found")

    if user_ctx.get("role") in ("client_admin", "client_standard") and user_ctx.get("client_folder_id"):
        folder_channels = set(get_folder_channel_ids(user_ctx["client_folder_id"]))
        if snapshot.get("channel_id") not in folder_channels:
            raise HTTPException(status_code=403, detail="Access denied: Context snapshot does not belong to your assigned folder.")

    return JSONResponse(content={"snapshot": serialize_data(snapshot)})


@context_router.get("", response_class=HTMLResponse)
async def context_inspector_page():
    html_content = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <title>JTS PowerTool • Claude Context Inspector</title>
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: {
              50: '#eef2ff',
              500: '#6366f1',
              600: '#4f46e5',
              700: '#4338ca',
            },
            surface: {
              900: '#0b0f19',
              800: '#111827',
              750: '#172033',
              700: '#1f2937',
              600: '#374151',
            }
          },
          fontFamily: {
            mono: ['JetBrains Mono', 'Fira Code', 'Courier New', 'monospace'],
          }
        }
      }
    }
  </script>
  <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');
    body { font-family: 'Inter', sans-serif; }
    .custom-scroll::-webkit-scrollbar { width: 6px; height: 6px; }
    .dark .custom-scroll::-webkit-scrollbar-track { background: #111827; }
    .dark .custom-scroll::-webkit-scrollbar-thumb { background: #374151; border-radius: 4px; }
    .dark .custom-scroll::-webkit-scrollbar-thumb:hover { background: #4b5563; }
    .custom-scroll::-webkit-scrollbar-track { background: #f1f5f9; }
    .custom-scroll::-webkit-scrollbar-thumb { background: #cbd5e1; border-radius: 4px; }
    .pulse-dot { animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite; }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
  </style>
</head>
<body class="bg-slate-50 dark:bg-surface-900 text-slate-800 dark:text-slate-100 min-h-screen flex flex-col transition-colors duration-200">

  <!-- TOP HEADER -->
  <header class="border-b border-slate-200 dark:border-surface-700 bg-white/90 dark:bg-surface-800/80 backdrop-blur sticky top-0 z-30 px-6 py-3.5 shadow-sm transition-colors duration-200">
    <div class="max-w-[1600px] mx-auto flex flex-wrap items-center justify-between gap-4">
      <div class="flex items-center space-x-3">
        <div class="h-9 w-9 rounded-lg bg-gradient-to-tr from-purple-600 to-indigo-500 flex items-center justify-center font-black text-white text-lg shadow-lg shadow-indigo-500/20">
          🧠
        </div>
        <div>
          <div class="flex items-center space-x-2">
            <h1 class="text-base font-bold tracking-tight text-slate-900 dark:text-white">Claude Context Inspector</h1>
            <span class="text-xs px-2 py-0.5 rounded-full bg-purple-100 dark:bg-purple-950/70 text-purple-700 dark:text-purple-300 font-semibold border border-purple-200 dark:border-purple-500/40">Latest Memory Slice</span>
          </div>
          <p class="text-xs text-slate-500 dark:text-slate-400">Live & Permanent Audit of Messages Passed to Claude</p>
        </div>
      </div>

      <!-- NAVIGATION & CONTROLS -->
      <div class="flex items-center flex-wrap gap-2.5">
        <!-- LINK TO TELEMETRY LOGS -->
        <a href="/logs" class="px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 dark:bg-surface-700 dark:hover:bg-surface-600 text-xs font-semibold text-slate-700 dark:text-slate-200 transition border border-slate-200 dark:border-surface-600 flex items-center space-x-1.5 shadow-sm">
          <span>⚡</span>
          <span>Live Logs</span>
        </a>

        <!-- LINK TO DATABASE TABLES -->
        <a href="/database" class="px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 dark:bg-surface-700 dark:hover:bg-surface-600 text-xs font-semibold text-slate-700 dark:text-slate-200 transition border border-slate-200 dark:border-surface-600 flex items-center space-x-1.5 shadow-sm">
          <span>🗄️</span>
          <span>Database Explorer</span>
        </a>

        <!-- AUTO REFRESH TOGGLE -->
        <button id="autoRefreshBtn" onclick="toggleAutoRefresh()" class="px-3 py-1.5 rounded-lg bg-emerald-50 hover:bg-emerald-100 dark:bg-emerald-950/70 dark:hover:bg-emerald-900/80 text-xs font-semibold text-emerald-700 dark:text-emerald-300 transition border border-emerald-200 dark:border-emerald-500/40 flex items-center space-x-1.5 shadow-sm">
          <span class="h-2 w-2 rounded-full bg-emerald-500 pulse-dot"></span>
          <span id="autoRefreshLabel">Auto-Refresh: ON (3s)</span>
        </button>

        <!-- MANUAL REFRESH -->
        <button onclick="loadLatestContext()" class="px-3 py-1.5 rounded-lg bg-brand-600 hover:bg-brand-700 text-xs font-semibold text-white transition border border-brand-500 flex items-center space-x-1.5 shadow-sm">
          <span>🔄</span>
          <span>Refresh Now</span>
        </button>

        <!-- THEME TOGGLE BUTTON -->
        <button id="themeToggleBtn" onclick="toggleTheme()" class="px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 dark:bg-surface-700 dark:hover:bg-surface-600 text-xs font-medium text-slate-700 dark:text-slate-200 transition border border-slate-200 dark:border-surface-600 flex items-center space-x-1.5 shadow-sm">
          <span id="themeIcon">🌙</span>
          <span id="themeLabel">Dark</span>
        </button>
      </div>
    </div>
  </header>

  <!-- MAIN CONTENT CONTAINER -->
  <main class="max-w-[1600px] w-full mx-auto p-6 flex-1 flex flex-col gap-6">

    <!-- METRICS & STATUS CARDS BAR -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-4">
      <!-- CARD 1: RUN ID & TIME -->
      <div class="rounded-xl border border-slate-200 dark:border-surface-700 bg-white dark:bg-surface-800 p-4 shadow-sm">
        <div class="text-xs font-medium text-slate-500 dark:text-slate-400">Snapshot ID & Execution</div>
        <div class="mt-1 flex items-baseline justify-between">
          <span id="snapshotId" class="text-xl font-bold font-mono text-purple-600 dark:text-purple-400">#--</span>
          <span id="snapshotTime" class="text-xs text-slate-500 dark:text-slate-400">Loading...</span>
        </div>
        <div id="snapshotRelative" class="mt-1 text-[11px] text-slate-400 font-mono truncate">Waiting for data...</div>
      </div>

      <!-- CARD 2: TRIGGER SPEAKER -->
      <div class="rounded-xl border border-slate-200 dark:border-surface-700 bg-white dark:bg-surface-800 p-4 shadow-sm">
        <div class="text-xs font-medium text-slate-500 dark:text-slate-400">Triggered By</div>
        <div id="speakerName" class="mt-1 text-base font-bold text-slate-900 dark:text-white truncate">--</div>
        <div id="speakerId" class="mt-1 text-[11px] text-slate-400 font-mono truncate">User ID: --</div>
      </div>

      <!-- CARD 3: CHANNEL & THREAD -->
      <div class="rounded-xl border border-slate-200 dark:border-surface-700 bg-white dark:bg-surface-800 p-4 shadow-sm">
        <div class="text-xs font-medium text-slate-500 dark:text-slate-400">Channel / Thread</div>
        <div id="channelId" class="mt-1 text-sm font-semibold font-mono text-slate-800 dark:text-slate-200 truncate">--</div>
        <div id="threadTs" class="mt-1 text-[11px] text-slate-400 font-mono truncate">Thread: --</div>
      </div>

      <!-- CARD 4: CONTEXT TURNS COUNT -->
      <div class="rounded-xl border border-slate-200 dark:border-surface-700 bg-white dark:bg-surface-800 p-4 shadow-sm">
        <div class="text-xs font-medium text-slate-500 dark:text-slate-400">Messages in Context</div>
        <div class="mt-1 flex items-baseline space-x-2">
          <span id="contextCount" class="text-xl font-bold font-mono text-emerald-600 dark:text-emerald-400">0</span>
          <span class="text-xs text-slate-500">turns passed to Claude</span>
        </div>
        <div id="modelName" class="mt-1 text-[11px] text-slate-400 font-mono truncate">Model: claude-haiku</div>
      </div>

      <!-- CARD 5: ATTACHMENTS INCLUDED -->
      <div class="rounded-xl border border-slate-200 dark:border-surface-700 bg-white dark:bg-surface-800 p-4 shadow-sm">
        <div class="text-xs font-medium text-slate-500 dark:text-slate-400">Files / Documents</div>
        <div id="filesCount" class="mt-1 text-base font-bold text-indigo-600 dark:text-indigo-400">0 Files</div>
        <div id="filesList" class="mt-1 text-[11px] text-slate-400 font-mono truncate">None attached</div>
      </div>
    </div>

    <!-- MAIN TWO-COLUMN LAYOUT -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-6 flex-1">

      <!-- LEFT COLUMN: CONTEXT MESSAGE LIST (8 COLS) -->
      <div class="lg:col-span-8 flex flex-col gap-4">
        <div class="flex items-center justify-between">
          <div class="flex items-center space-x-2">
            <h2 class="text-sm font-bold tracking-tight text-slate-900 dark:text-white uppercase flex items-center space-x-2">
              <span>💬</span>
              <span>Exact Messages Passed to Claude</span>
            </h2>
            <span id="turnsBadge" class="text-xs px-2 py-0.5 rounded-full bg-slate-100 dark:bg-surface-700 text-slate-600 dark:text-slate-300 font-mono font-medium">0 turns</span>
          </div>

          <div class="flex items-center space-x-2 text-xs text-slate-500">
            <span>Chronological order (Top to Bottom)</span>
          </div>
        </div>

        <!-- MESSAGE CONTAINERS WRAPPER -->
        <div id="messagesContainer" class="flex flex-col gap-4">
          <div class="rounded-xl border border-slate-200 dark:border-surface-700 bg-white dark:bg-surface-800 p-8 text-center text-slate-400">
            Loading context snapshot...
          </div>
        </div>

        <!-- COLLAPSIBLE SYSTEM PROMPT PANEL -->
        <div class="rounded-xl border border-slate-200 dark:border-surface-700 bg-white dark:bg-surface-800 overflow-hidden shadow-sm">
          <button onclick="toggleSystemPrompt()" class="w-full px-4 py-3 flex items-center justify-between text-left bg-slate-50 hover:bg-slate-100 dark:bg-surface-750 dark:hover:bg-surface-700 transition">
            <div class="flex items-center space-x-2">
              <span class="text-sm font-semibold text-slate-700 dark:text-slate-200">⚙️ Active System Prompt Passed to Claude</span>
              <span class="text-[11px] text-slate-400 font-mono">(Click to expand)</span>
            </div>
            <span id="sysPromptArrow" class="text-xs text-slate-400 font-mono">▼</span>
          </button>
          <div id="systemPromptBody" class="hidden p-4 border-t border-slate-200 dark:border-surface-700 bg-slate-50/50 dark:bg-surface-900/40">
            <pre id="systemPromptText" class="text-xs font-mono text-slate-700 dark:text-slate-300 whitespace-pre-wrap leading-relaxed max-h-[350px] overflow-y-auto custom-scroll p-3 bg-white dark:bg-surface-800 rounded-lg border border-slate-200 dark:border-surface-700">Loading system prompt...</pre>
          </div>
        </div>
      </div>

      <!-- RIGHT COLUMN: RECENT CONTEXT RUNS HISTORY (4 COLS) -->
      <div class="lg:col-span-4 flex flex-col gap-4">
        <div class="flex items-center justify-between">
          <h2 class="text-sm font-bold tracking-tight text-slate-900 dark:text-white uppercase flex items-center space-x-2">
            <span>📜</span>
            <span>Historical Context Runs</span>
          </h2>
          <span class="text-xs text-slate-400">Permanently in PostgreSQL</span>
        </div>

        <div class="rounded-xl border border-slate-200 dark:border-surface-700 bg-white dark:bg-surface-800 p-3 shadow-sm flex-1 flex flex-col">
          <p class="text-[11px] text-slate-500 dark:text-slate-400 px-2 py-1 mb-2">
            Click any run below to inspect its exact context snapshot:
          </p>
          <div id="historyList" class="flex flex-col gap-2 overflow-y-auto custom-scroll max-h-[700px] pr-1">
            <div class="text-xs text-slate-400 p-4 text-center">Loading past runs...</div>
          </div>
        </div>
      </div>

    </div>
  </main>

  <!-- JAVASCRIPT APP LOGIC -->
  <script>
    let currentSnapshotId = null;
    let autoRefreshInterval = null;
    let isAutoRefresh = true;

    // Theme toggle handling
    function initTheme() {
      const saved = localStorage.getItem('jts_theme') || 'dark';
      applyTheme(saved);
    }

    function applyTheme(theme) {
      const html = document.documentElement;
      const icon = document.getElementById('themeIcon');
      const label = document.getElementById('themeLabel');
      if (theme === 'dark') {
        html.classList.add('dark');
        icon.textContent = '☀️';
        label.textContent = 'Light';
      } else {
        html.classList.remove('dark');
        icon.textContent = '🌙';
        label.textContent = 'Dark';
      }
      localStorage.setItem('jts_theme', theme);
    }

    function toggleTheme() {
      const isDark = document.documentElement.classList.contains('dark');
      applyTheme(isDark ? 'light' : 'dark');
    }

    function toggleSystemPrompt() {
      const body = document.getElementById('systemPromptBody');
      const arrow = document.getElementById('sysPromptArrow');
      if (body.classList.contains('hidden')) {
        body.classList.remove('hidden');
        arrow.textContent = '▲';
      } else {
        body.classList.add('hidden');
        arrow.textContent = '▼';
      }
    }

    function toggleAutoRefresh() {
      isAutoRefresh = !isAutoRefresh;
      const btn = document.getElementById('autoRefreshBtn');
      const label = document.getElementById('autoRefreshLabel');
      if (isAutoRefresh) {
        btn.className = "px-3 py-1.5 rounded-lg bg-emerald-50 hover:bg-emerald-100 dark:bg-emerald-950/70 dark:hover:bg-emerald-900/80 text-xs font-semibold text-emerald-700 dark:text-emerald-300 transition border border-emerald-200 dark:border-emerald-500/40 flex items-center space-x-1.5 shadow-sm";
        label.textContent = "Auto-Refresh: ON (3s)";
        startPolling();
      } else {
        btn.className = "px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 dark:bg-surface-700 dark:hover:bg-surface-600 text-xs font-medium text-slate-700 dark:text-slate-300 transition border border-slate-200 dark:border-surface-600 flex items-center space-x-1.5 shadow-sm";
        label.textContent = "Auto-Refresh: PAUSED";
        clearInterval(autoRefreshInterval);
      }
    }

    function startPolling() {
      clearInterval(autoRefreshInterval);
      autoRefreshInterval = setInterval(() => {
        if (isAutoRefresh) {
          loadLatestContext(true);
        }
      }, 3000);
    }

    function formatTime(isoStr) {
      if (!isoStr) return '--';
      try {
        const d = new Date(isoStr);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
      } catch (e) {
        return isoStr;
      }
    }

    function escapeHtml(str) {
      if (!str) return '';
      return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
    }

    async function loadLatestContext(silent = false) {
      try {
        const res = await fetch('/context/api/latest');
        const data = await res.json();
        const snap = data.snapshot;
        if (!snap) {
          document.getElementById('messagesContainer').innerHTML = `
            <div class="rounded-xl border border-slate-200 dark:border-surface-700 bg-white dark:bg-surface-800 p-8 text-center text-slate-400">
              No Claude context snapshots recorded yet. When you interact with the bot in Slack, the exact context slice sent to Claude will appear here automatically!
            </div>
          `;
          return;
        }

        // Only re-render if snapshot ID changed or if manual refresh
        if (!silent || snap.id !== currentSnapshotId) {
          currentSnapshotId = snap.id;
          renderSnapshot(snap);
          loadHistory();
        }
      } catch (err) {
        console.error("Failed to load latest snapshot:", err);
      }
    }

    async function loadSnapshotById(id) {
      try {
        const res = await fetch(`/context/api/snapshot/${id}`);
        const data = await res.json();
        if (data.snapshot) {
          currentSnapshotId = data.snapshot.id;
          renderSnapshot(data.snapshot);
          highlightActiveHistoryItem(id);
        }
      } catch (err) {
        console.error(`Failed to load snapshot ${id}:`, err);
      }
    }

    async function loadHistory() {
      try {
        const res = await fetch('/context/api/history?limit=25');
        const data = await res.json();
        const historyList = document.getElementById('historyList');
        if (!data.history || data.history.length === 0) {
          historyList.innerHTML = '<div class="text-xs text-slate-400 p-3 text-center">No history yet</div>';
          return;
        }

        historyList.innerHTML = data.history.map(item => {
          const isActive = item.id === currentSnapshotId;
          const activeCls = isActive
            ? 'bg-purple-50 dark:bg-purple-950/60 border-purple-300 dark:border-purple-500/50 shadow-sm'
            : 'bg-slate-50 hover:bg-slate-100 dark:bg-surface-750 dark:hover:bg-surface-700 border-slate-200 dark:border-surface-700';

          const timeFormatted = formatTime(item.created_at);
          const promptShort = escapeHtml(item.prompt_text || 'Interaction');

          return `
            <button onclick="loadSnapshotById(${item.id})" class="w-full text-left p-2.5 rounded-lg border transition ${activeCls} flex flex-col gap-1">
              <div class="flex items-center justify-between text-xs">
                <span class="font-bold font-mono text-purple-600 dark:text-purple-400">#${item.id}</span>
                <span class="text-[11px] text-slate-400 font-mono">${timeFormatted}</span>
              </div>
              <div class="text-xs font-semibold text-slate-800 dark:text-slate-200 truncate">
                ${promptShort}
              </div>
              <div class="flex items-center justify-between text-[11px] text-slate-500 dark:text-slate-400">
                <span>👤 ${escapeHtml(item.user_name || 'User')}</span>
                <span class="font-mono bg-slate-200 dark:bg-surface-600 px-1.5 py-0.2 rounded">${item.context_count || 0} msgs</span>
              </div>
            </button>
          `;
        }).join('');
      } catch (err) {
        console.error("Failed to load history:", err);
      }
    }

    function highlightActiveHistoryItem(id) {
      const buttons = document.querySelectorAll('#historyList button');
      buttons.forEach(btn => {
        if (btn.textContent.includes(`#${id}`)) {
          btn.className = "w-full text-left p-2.5 rounded-lg border transition bg-purple-50 dark:bg-purple-950/60 border-purple-300 dark:border-purple-500/50 shadow-sm flex flex-col gap-1";
        }
      });
    }

    function renderSnapshot(snap) {
      // Header cards
      document.getElementById('snapshotId').textContent = `#${snap.id}`;
      document.getElementById('snapshotTime').textContent = formatTime(snap.created_at);
      document.getElementById('snapshotRelative').textContent = snap.created_at ? new Date(snap.created_at).toLocaleDateString() : '';
      
      document.getElementById('speakerName').textContent = snap.user_name || 'Unknown';
      document.getElementById('speakerId').textContent = `User ID: ${snap.user_id || '--'}`;

      document.getElementById('channelId').textContent = `#${snap.channel_id || 'unknown'}`;
      document.getElementById('threadTs').textContent = snap.thread_ts ? `Thread: ${snap.thread_ts}` : 'Channel Root';

      document.getElementById('contextCount').textContent = snap.context_count || 0;
      document.getElementById('modelName').textContent = `Model: ${snap.model || 'Claude'}`;
      document.getElementById('turnsBadge').textContent = `${snap.context_count || 0} turns`;

      const files = snap.files_included || [];
      document.getElementById('filesCount').textContent = files.length > 0 ? `${files.length} Attached` : '0 Files';
      document.getElementById('filesList').textContent = files.length > 0 ? files.join(', ') : 'None attached';

      // System Prompt
      document.getElementById('systemPromptText').textContent = snap.system_prompt || 'No system prompt recorded.';

      // Render Messages
      const container = document.getElementById('messagesContainer');
      const messages = snap.messages_sent || [];

      if (messages.length === 0) {
        container.innerHTML = `
          <div class="rounded-xl border border-slate-200 dark:border-surface-700 bg-white dark:bg-surface-800 p-8 text-center text-slate-400">
            No message turns were passed in this snapshot.
          </div>
        `;
        return;
      }

      container.innerHTML = messages.map((m, idx) => {
        const isAssistant = (m.role === 'assistant');
        const isLast = (idx === messages.length - 1);
        const speaker = m.speaker || (isAssistant ? 'Bot' : 'User');
        const roleLabel = isAssistant ? 'ASSISTANT (Prior Bot Response Anchor)' : (isLast ? 'USER (Current Request Trigger)' : 'USER (Human Turn)');

        const roleBadgeCls = isAssistant
          ? 'bg-purple-100 dark:bg-purple-950/70 text-purple-700 dark:text-purple-300 border-purple-200 dark:border-purple-500/40'
          : (isLast ? 'bg-blue-100 dark:bg-blue-950/70 text-blue-700 dark:text-blue-300 border-blue-200 dark:border-blue-500/40 font-bold' : 'bg-emerald-100 dark:bg-emerald-950/70 text-emerald-700 dark:text-emerald-300 border-emerald-200 dark:border-emerald-500/40');

        const cardBorderCls = isLast
          ? 'border-blue-400 dark:border-blue-500/60 shadow-md ring-1 ring-blue-400/20'
          : (isAssistant ? 'border-purple-200 dark:border-purple-500/30' : 'border-slate-200 dark:border-surface-700');

        const avatar = isAssistant ? '🤖' : '👤';
        const contentStr = typeof m.content === 'object' ? JSON.stringify(m.content, null, 2) : String(m.content || '');

        return `
          <div class="rounded-xl border ${cardBorderCls} bg-white dark:bg-surface-800 overflow-hidden shadow-sm transition">
            <!-- TURN HEADER -->
            <div class="px-4 py-3 bg-slate-50/80 dark:bg-surface-750/70 border-b border-slate-200 dark:border-surface-700 flex items-center justify-between flex-wrap gap-2">
              <div class="flex items-center space-x-2">
                <span class="text-base">${avatar}</span>
                <span class="text-xs font-bold text-slate-800 dark:text-white font-mono">${escapeHtml(speaker)}</span>
                <span class="text-[11px] px-2 py-0.5 rounded-full border font-semibold ${roleBadgeCls}">${roleLabel}</span>
              </div>
              <div class="text-[11px] font-mono text-slate-400">
                Turn ${idx + 1} of ${messages.length}
              </div>
            </div>

            <!-- TURN CONTENT -->
            <div class="p-4">
              <pre class="text-xs font-mono text-slate-800 dark:text-slate-200 whitespace-pre-wrap break-words leading-relaxed custom-scroll max-h-[500px] overflow-y-auto">${escapeHtml(contentStr)}</pre>
            </div>
          </div>
        `;
      }).join('');
    }

    document.addEventListener('DOMContentLoaded', () => {
      initTheme();
      loadLatestContext();
      startPolling();
    });
  </script>
</body>
</html>
"""
    return HTMLResponse(content=html_content)
