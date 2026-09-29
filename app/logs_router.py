import json
from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from app.log_stream import LOG_BUFFER, clear_log_buffer, get_metrics_snapshot, subscribe_log_stream

logs_router = APIRouter(prefix="/logs", tags=["Monitoring"])

@logs_router.get("/api/feed")
async def get_log_feed():
    return JSONResponse(content=list(reversed(LOG_BUFFER)))

@logs_router.get("/api/metrics")
async def get_metrics():
    return JSONResponse(content=get_metrics_snapshot())

@logs_router.post("/api/clear")
async def clear_logs():
    clear_log_buffer()
    return JSONResponse(content={"status": "ok", "message": "Logs permanently cleared"})

@logs_router.get("/stream")
async def stream_logs():
    return StreamingResponse(
        subscribe_log_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )

@logs_router.get("", response_class=HTMLResponse)
async def logs_dashboard():
    html_content = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <title>JTS PowerTool • Live Telemetry Log Monitor</title>
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
    .custom-scroll::-webkit-scrollbar-thumb:hover { background: #94a3b8; }
    .pulse-dot { animation: pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite; }
    @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
  </style>
</head>
<body class="bg-slate-50 dark:bg-surface-900 text-slate-800 dark:text-slate-100 min-h-screen flex flex-col transition-colors duration-200 selection:bg-brand-500 selection:text-white">

  <!-- TOP HEADER -->
  <header class="border-b border-slate-200 dark:border-surface-700 bg-white/90 dark:bg-surface-800/80 backdrop-blur sticky top-0 z-30 px-6 py-3.5 shadow-sm transition-colors duration-200">
    <div class="max-w-[1600px] mx-auto flex flex-wrap items-center justify-between gap-4">
      <div class="flex items-center space-x-3">
        <div class="h-9 w-9 rounded-lg bg-gradient-to-tr from-brand-600 to-cyan-500 flex items-center justify-center font-black text-white text-lg shadow-lg shadow-brand-500/20">
          ⚡
        </div>
        <div>
          <div class="flex items-center space-x-2">
            <h1 class="text-base font-bold tracking-tight text-slate-900 dark:text-white">JTS PowerTool</h1>
            <span class="text-xs px-2 py-0.5 rounded-full bg-slate-100 dark:bg-surface-700 text-slate-600 dark:text-slate-300 font-medium border border-slate-200 dark:border-surface-600">Telemetry Monitor</span>
          </div>
          <p class="text-xs text-slate-500 dark:text-slate-400">Real-Time Slack • Claude Engine • Session Mapping</p>
        </div>
      </div>

      <!-- STATUS & CONTROLS -->
      <div class="flex items-center space-x-3">
        <div id="connStatus" class="flex items-center space-x-2 px-3 py-1.5 rounded-full bg-emerald-50 dark:bg-emerald-950/60 border border-emerald-200 dark:border-emerald-500/30 text-emerald-700 dark:text-emerald-400 text-xs font-semibold transition-colors">
          <span class="h-2 w-2 rounded-full bg-emerald-500 dark:bg-emerald-400 pulse-dot"></span>
          <span id="connText">STREAMING LIVE (SSE)</span>
        </div>

        <!-- VIEW DATABASE TABLES BUTTON -->
        <a href="/database" class="px-3 py-1.5 rounded-lg bg-indigo-50 hover:bg-indigo-100 dark:bg-indigo-950/70 dark:hover:bg-indigo-900/80 text-xs font-semibold text-indigo-700 dark:text-indigo-300 transition border border-indigo-200 dark:border-indigo-500/40 flex items-center space-x-1.5 shadow-sm">
          <span>🗄️</span>
          <span>Database Tables</span>
        </a>

        <!-- VIEW CLAUDE CONTEXT BUTTON -->
        <a href="/context" class="px-3 py-1.5 rounded-lg bg-purple-50 hover:bg-purple-100 dark:bg-purple-950/70 dark:hover:bg-purple-900/80 text-xs font-semibold text-purple-700 dark:text-purple-300 transition border border-purple-200 dark:border-purple-500/40 flex items-center space-x-1.5 shadow-sm">
          <span>🧠</span>
          <span>Claude Context</span>
        </a>

        <!-- THEME TOGGLE BUTTON -->
        <button id="themeToggleBtn" onclick="toggleTheme()" class="px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 dark:bg-surface-700 dark:hover:bg-surface-600 text-xs font-medium text-slate-700 dark:text-slate-200 transition border border-slate-200 dark:border-surface-600 flex items-center space-x-1.5 shadow-sm" title="Toggle Light / Dark Mode">
          <span id="themeIcon">🌙</span>
          <span id="themeLabel">Dark</span>
        </button>

        <button id="pauseBtn" onclick="togglePause()" class="px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 dark:bg-surface-700 dark:hover:bg-surface-600 text-xs font-medium text-slate-700 dark:text-slate-200 transition border border-slate-200 dark:border-surface-600 flex items-center space-x-1.5 shadow-sm">
          <span id="pauseIcon">⏸</span> <span id="pauseLabel">Pause</span>
        </button>

        <button onclick="clearLogsUI()" class="px-3 py-1.5 rounded-lg bg-slate-100 hover:bg-slate-200 dark:bg-surface-700 dark:hover:bg-surface-600 text-xs font-medium text-slate-700 dark:text-slate-200 transition border border-slate-200 dark:border-surface-600 shadow-sm">
          🧹 Clear Feed
        </button>
      </div>
    </div>
  </header>

  <main class="flex-1 max-w-[1600px] w-full mx-auto p-6 space-y-4">

    <!-- FILTER & SEARCH BAR -->
    <div class="bg-white dark:bg-surface-800 border border-slate-200 dark:border-surface-700 rounded-xl p-4 flex flex-wrap items-center justify-between gap-4 shadow-sm transition-colors duration-200">
      <div class="flex items-center space-x-2 flex-1 min-w-[280px]">
        <span class="text-slate-400 text-sm">🔍</span>
        <input id="searchInput" type="text" placeholder="Search by Thread TS, Session UUID, Action, or Event ID..." 
               class="w-full bg-slate-50 dark:bg-surface-900 border border-slate-200 dark:border-surface-700 rounded-lg px-3.5 py-2 text-xs text-slate-800 dark:text-slate-200 placeholder-slate-400 dark:placeholder-slate-500 focus:outline-none focus:border-brand-500 font-mono transition-colors">
      </div>

      <!-- CATEGORY PILLS -->
      <div class="flex items-center space-x-1.5 overflow-x-auto text-xs">
        <button onclick="setFilter('ALL')" id="filter-ALL" class="filter-pill px-3 py-1.5 rounded-lg bg-brand-600 text-white font-medium border border-brand-500 shadow-sm">All</button>
        <button onclick="setFilter('SLACK')" id="filter-SLACK" class="filter-pill px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-surface-700 text-slate-700 dark:text-slate-300 hover:text-brand-600 dark:hover:text-white border border-slate-200 dark:border-surface-600">Slack</button>
        <button onclick="setFilter('CLAUDE')" id="filter-CLAUDE" class="filter-pill px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-surface-700 text-slate-700 dark:text-slate-300 hover:text-brand-600 dark:hover:text-white border border-slate-200 dark:border-surface-600">Claude</button>
        <button onclick="setFilter('DEDUPLICATION')" id="filter-DEDUPLICATION" class="filter-pill px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-surface-700 text-slate-700 dark:text-slate-300 hover:text-brand-600 dark:hover:text-white border border-slate-200 dark:border-surface-600">Dedup</button>
        <button onclick="setFilter('FILTER')" id="filter-FILTER" class="filter-pill px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-surface-700 text-slate-700 dark:text-slate-300 hover:text-brand-600 dark:hover:text-white border border-slate-200 dark:border-surface-600">Bots</button>
        <button onclick="setFilter('DATABASE')" id="filter-DATABASE" class="filter-pill px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-surface-700 text-slate-700 dark:text-slate-300 hover:text-brand-600 dark:hover:text-white border border-slate-200 dark:border-surface-600">Database</button>
      </div>

      <div class="flex items-center space-x-3 text-xs text-slate-500 dark:text-slate-400">
        <label class="flex items-center space-x-1.5 cursor-pointer">
          <input type="checkbox" id="autoScrollCheck" checked class="rounded bg-slate-100 dark:bg-surface-900 border-slate-300 dark:border-surface-700 text-brand-600 focus:ring-0">
          <span>Auto-scroll</span>
        </label>
        <span class="text-slate-300 dark:text-surface-600">|</span>
        <span id="logCountLabel" class="font-mono">0 events</span>
      </div>
    </div>

    <!-- LOG STREAM TABLE -->
    <div class="bg-white dark:bg-surface-800 border border-slate-200 dark:border-surface-700 rounded-xl overflow-hidden shadow-sm dark:shadow-2xl flex flex-col transition-colors duration-200">
      <div class="overflow-x-auto custom-scroll" style="max-height: calc(100vh - 190px);">
        <table class="w-full text-left border-collapse">
          <thead class="bg-slate-100 dark:bg-surface-750 text-[11px] font-semibold text-slate-600 dark:text-slate-400 uppercase tracking-wider sticky top-0 z-10 border-b border-slate-200 dark:border-surface-700 transition-colors">
            <tr>
              <th class="py-3 px-4 w-[160px]">Timestamp</th>
              <th class="py-3 px-4 w-[170px]">Action / Event</th>
              <th class="py-3 px-4 w-[180px]">Slack Thread TS</th>
              <th class="py-3 px-4 w-[280px]">Claude Session UUID</th>
              <th class="py-3 px-4 w-[200px]">Event ID</th>
              <th class="py-3 px-4 min-w-[320px]">Telemetry & Verification Details</th>
            </tr>
          </thead>
          <tbody id="logTableBody" class="divide-y divide-slate-100 dark:divide-surface-700/60 font-mono text-xs">
            <tr>
              <td colspan="6" class="py-16 text-center text-slate-400 dark:text-slate-500 font-sans">
                <div class="flex flex-col items-center justify-center space-y-2">
                  <div class="h-6 w-6 border-2 border-brand-500 border-t-transparent rounded-full animate-spin"></div>
                  <span>Awaiting live telemetry events from Slack / Claude...</span>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

  </main>

  <!-- JAVASCRIPT SSE STREAM, THEME & RENDERING -->
  <script>
    let logs = [];
    let isPaused = false;
    let currentCategory = 'ALL';
    let eventSource = null;

    // THEME HANDLING
    function initTheme() {
      const savedTheme = localStorage.getItem('theme');
      const prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
      const isDark = savedTheme ? savedTheme === 'dark' : prefersDark;
      setTheme(isDark ? 'dark' : 'light');
    }

    function setTheme(mode) {
      const html = document.documentElement;
      const themeIcon = document.getElementById('themeIcon');
      const themeLabel = document.getElementById('themeLabel');

      if (mode === 'dark') {
        html.classList.add('dark');
        localStorage.setItem('theme', 'dark');
        themeIcon.innerText = '🌙';
        themeLabel.innerText = 'Dark';
      } else {
        html.classList.remove('dark');
        localStorage.setItem('theme', 'light');
        themeIcon.innerText = '☀️';
        themeLabel.innerText = 'Light';
      }
      renderLogs();
    }

    function toggleTheme() {
      const isDark = document.documentElement.classList.contains('dark');
      setTheme(isDark ? 'light' : 'dark');
    }

    function getActionBadge(action) {
      switch (action) {
        case 'RAG_CONTEXT_RETRIEVED':
          return '<span class="px-2 py-0.5 rounded bg-purple-100 dark:bg-purple-950/80 text-purple-800 dark:text-purple-300 border border-purple-300 dark:border-purple-500/40 text-[11px] font-bold">🧬 LOCAL RAG AUDIT</span>';
        case 'SESSION_CREATED':
          return '<span class="px-2 py-0.5 rounded bg-indigo-50 dark:bg-indigo-950/80 text-indigo-700 dark:text-indigo-300 border border-indigo-200 dark:border-indigo-500/40 text-[11px] font-bold">✨ NEW SESSION</span>';
        case 'SESSION_RESUMED':
          return '<span class="px-2 py-0.5 rounded bg-cyan-50 dark:bg-cyan-950/80 text-cyan-700 dark:text-cyan-300 border border-cyan-200 dark:border-cyan-500/40 text-[11px] font-bold">🔁 RESUMED</span>';
        case 'DUPLICATE_IGNORED':
          return '<span class="px-2 py-0.5 rounded bg-amber-50 dark:bg-amber-950/80 text-amber-800 dark:text-amber-300 border border-amber-200 dark:border-amber-500/40 text-[11px] font-bold">🛡️ DUPLICATE SKIPPED</span>';
        case 'BOT_IGNORED':
          return '<span class="px-2 py-0.5 rounded bg-pink-50 dark:bg-pink-950/80 text-pink-700 dark:text-pink-300 border border-pink-200 dark:border-pink-500/40 text-[11px] font-bold">🤖 BOT FILTERED</span>';
        case 'EVENT_RECEIVED':
          return '<span class="px-2 py-0.5 rounded bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-300 border border-slate-300 dark:border-slate-600 text-[11px] font-medium">📥 SLACK INBOUND</span>';
        case 'RESPONSE_DISPATCHED':
          return '<span class="px-2 py-0.5 rounded bg-emerald-50 dark:bg-emerald-950/80 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-500/40 text-[11px] font-bold">🚀 DELIVERED</span>';
        case 'MESSAGE_PERSISTED':
          return '<span class="px-2 py-0.5 rounded bg-blue-50 dark:bg-blue-950/80 text-blue-700 dark:text-blue-300 border border-blue-200 dark:border-blue-500/40 text-[11px] font-medium">💾 MEMORY SAVED</span>';
        case 'DB_ERROR':
        case 'EXECUTION_ERROR':
        case 'SLACK_API_ERROR':
          return '<span class="px-2 py-0.5 rounded bg-rose-50 dark:bg-rose-950/80 text-rose-700 dark:text-rose-300 border border-rose-200 dark:border-rose-500/40 text-[11px] font-bold">⚠️ ERROR</span>';
        default:
          return `<span class="px-2 py-0.5 rounded bg-slate-100 dark:bg-surface-700 text-slate-700 dark:text-slate-300 border border-slate-300 dark:border-surface-600 text-[11px] font-medium">${action || 'INFO'}</span>`;
      }
    }

    function formatId(id) {
      if (!id || id === '-') return '<span class="text-slate-400 dark:text-slate-600">-</span>';
      return `<span class="text-slate-700 dark:text-slate-300 select-all" title="${id}">${id}</span>`;
    }

    function escapeHtml(str) {
      if (!str) return '';
      return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    }

    function formatTimestamp(isoStr) {
      if (!isoStr || isoStr === '-') return '<span class="text-slate-400 dark:text-slate-600">-</span>';
      try {
        const d = new Date(isoStr);
        if (isNaN(d.getTime())) return `<span class="text-slate-600 dark:text-slate-400 font-mono text-[11px]">${escapeHtml(isoStr)}</span>`;
        const pad = (n) => String(n).padStart(2, '0');
        const y = d.getFullYear();
        const m = pad(d.getMonth() + 1);
        const day = pad(d.getDate());
        const h = pad(d.getHours());
        const min = pad(d.getMinutes());
        const s = pad(d.getSeconds());
        return `<span class="text-slate-600 dark:text-slate-400 font-mono text-[11px]" title="${d.toString()}">${y}-${m}-${day} ${h}:${min}:${s}</span>`;
      } catch (e) {
        return `<span class="text-slate-500 font-mono text-[11px]">${escapeHtml(isoStr)}</span>`;
      }
    }

    function renderLogs() {
      const tbody = document.getElementById('logTableBody');
      const search = document.getElementById('searchInput').value.toLowerCase().trim();

      const filtered = logs.filter(l => {
        if (currentCategory !== 'ALL' && l.category !== currentCategory) return false;
        if (!search) return true;
        return (
          (l.message && l.message.toLowerCase().includes(search)) ||
          (l.thread_id && l.thread_id.toLowerCase().includes(search)) ||
          (l.session_id && l.session_id.toLowerCase().includes(search)) ||
          (l.event_id && l.event_id.toLowerCase().includes(search)) ||
          (l.action && l.action.toLowerCase().includes(search))
        );
      });

      document.getElementById('logCountLabel').innerText = `${logs.length} events`;

      if (filtered.length === 0) {
        tbody.innerHTML = `
          <tr>
            <td colspan="6" class="py-16 text-center text-slate-400 dark:text-slate-500 font-sans">
              No matching telemetry events found.
            </td>
          </tr>
        `;
        return;
      }

      tbody.innerHTML = filtered.map(l => {
        const isRag = l.action === 'RAG_CONTEXT_RETRIEVED';
        const rowBg = isRag ? 'bg-purple-50/50 dark:bg-purple-950/25' :
                      l.action === 'DUPLICATE_IGNORED' ? 'bg-amber-50/50 dark:bg-amber-950/20' : 
                      l.action === 'BOT_IGNORED' ? 'bg-pink-50/50 dark:bg-pink-950/20' :
                      l.action === 'SESSION_RESUMED' ? 'bg-cyan-50/50 dark:bg-cyan-950/20' :
                      l.action === 'SESSION_CREATED' ? 'bg-indigo-50/50 dark:bg-indigo-950/20' : '';

        return `
          <tr class="hover:bg-slate-100/70 dark:hover:bg-surface-700/50 transition border-b border-slate-100 dark:border-surface-700/40 ${rowBg}">
            <td class="py-2.5 px-4 whitespace-nowrap">${formatTimestamp(l.timestamp)}</td>
            <td class="py-2.5 px-4 whitespace-nowrap">${getActionBadge(l.action)}</td>
            <td class="py-2.5 px-4 text-indigo-700 dark:text-indigo-300 font-mono text-[11px]">${formatId(l.thread_id)}</td>
            <td class="py-2.5 px-4 text-cyan-700 dark:text-cyan-300 font-mono text-[11px]">${formatId(l.session_id)}</td>
            <td class="py-2.5 px-4 text-slate-500 dark:text-slate-400 font-mono text-[11px]">${formatId(l.event_id)}</td>
            <td class="py-2.5 px-4 text-slate-800 dark:text-slate-300 text-xs">
              ${isRag ? 
                `<pre class="font-mono text-[11px] bg-purple-50 dark:bg-purple-950/50 p-3 rounded-lg border border-purple-200 dark:border-purple-800/60 text-purple-950 dark:text-purple-200 whitespace-pre-wrap leading-relaxed shadow-sm font-semibold">${escapeHtml(l.message)}</pre>` :
                `<span class="font-sans">${escapeHtml(l.message)}</span>`
              }
            </td>
          </tr>
        `;
      }).join('');

      if (document.getElementById('autoScrollCheck').checked) {
        const container = tbody.closest('.custom-scroll');
        if (container) container.scrollTop = 0;
      }
    }

    function initSSE() {
      if (eventSource) {
        try { eventSource.close(); } catch(e) {}
      }

      eventSource = new EventSource("/logs/stream");

      eventSource.onopen = () => {
        document.getElementById('connStatus').className = "flex items-center space-x-2 px-3 py-1.5 rounded-full bg-emerald-50 dark:bg-emerald-950/60 border border-emerald-200 dark:border-emerald-500/30 text-emerald-700 dark:text-emerald-400 text-xs font-semibold transition-colors";
        document.getElementById('connText').innerText = "STREAMING LIVE (SSE)";
      };

      eventSource.onmessage = (e) => {
        if (isPaused) return;
        try {
          const entry = JSON.parse(e.data);
          if (entry.action === 'FEED_CLEARED') {
            logs = [];
            renderLogs();
            return;
          }
          logs.unshift(entry);
          if (logs.length > 1000) logs.pop();
          renderLogs();
        } catch (err) {
          // ignore keepalive pings
        }
      };

      eventSource.onerror = () => {
        document.getElementById('connStatus').className = "flex items-center space-x-2 px-3 py-1.5 rounded-full bg-amber-50 dark:bg-amber-950/60 border border-amber-200 dark:border-amber-500/30 text-amber-700 dark:text-amber-400 text-xs font-semibold transition-colors";
        document.getElementById('connText').innerText = "RECONNECTING...";
      };
    }

    async function loadInitialFeed() {
      try {
        const res = await fetch("/logs/api/feed");
        logs = await res.json();
        renderLogs();
      } catch (e) {
        console.error("Failed to load initial feed:", e);
      }
    }

    function togglePause() {
      isPaused = !isPaused;
      const lbl = document.getElementById('pauseLabel');
      const ico = document.getElementById('pauseIcon');
      lbl.innerText = isPaused ? "Resume" : "Pause";
      ico.innerText = isPaused ? "▶" : "⏸";
    }

    async function clearLogsUI() {
      try {
        await fetch("/logs/api/clear", { method: "POST" });
        logs = [];
        renderLogs();
      } catch (err) {
        console.error("Failed to clear server logs:", err);
      }
    }

    function setFilter(cat) {
      currentCategory = cat;
      document.querySelectorAll('.filter-pill').forEach(btn => {
        btn.className = "filter-pill px-3 py-1.5 rounded-lg bg-slate-100 dark:bg-surface-700 text-slate-700 dark:text-slate-300 hover:text-brand-600 dark:hover:text-white border border-slate-200 dark:border-surface-600";
      });
      const activeBtn = document.getElementById(`filter-${cat}`);
      if (activeBtn) {
        activeBtn.className = "filter-pill px-3 py-1.5 rounded-lg bg-brand-600 text-white font-medium border border-brand-500 shadow-sm";
      }
      renderLogs();
    }

    document.getElementById('searchInput').addEventListener('input', renderLogs);

    // Initial setup
    initTheme();
    loadInitialFeed();
    initSSE();
  </script>
</body>
</html>
"""
    return HTMLResponse(
        content=html_content,
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0"
        }
    )



