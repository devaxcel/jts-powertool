"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Database,
  Table as TableIcon,
  Search,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { fetchTables, fetchTableData, clearTableData, clearFullDatabase, deleteTableRow } from "@/lib/api";
import { TableInfo, TableColumn, formatLocalDateTime } from "@/lib/types";

function formatCellValue(val: any, colName?: string): { isObj: boolean; text: string; full: string } {
  if (val === null || val === undefined) {
    return { isObj: false, text: "null", full: "null" };
  }
  if (typeof val === "object") {
    try {
      const s = JSON.stringify(val);
      return { isObj: true, text: s.length > 35 ? s.slice(0, 35) + "..." : s, full: s };
    } catch {
      return { isObj: true, text: "[Object]", full: "[Object]" };
    }
  }
  const s = String(val);
  const col = (colName || "").toLowerCase();
  const isDateCol = col.includes("created_at") || col.includes("updated_at") || col.includes("timestamp") || col.includes("expires_at");
  const isDatePattern = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}/.test(s);
  if (isDateCol || isDatePattern) {
    const formatted = formatLocalDateTime(s);
    return { isObj: false, text: formatted, full: formatted };
  }
  return { isObj: false, text: s, full: s };
}

export default function DatabasePage() {
  const [tables, setTables] = useState<TableInfo[]>([]);
  const [selectedTable, setSelectedTable] = useState<string>("");
  const [columns, setColumns] = useState<TableColumn[]>([]);
  const [rows, setRows] = useState<any[]>([]);
  const [totalRows, setTotalRows] = useState<number>(0);
  const [loading, setLoading] = useState(true);
  const [tableLoading, setTableLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState(false);
  const [deletingRowId, setDeletingRowId] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

  const loadTables = useCallback(async () => {
    setLoading(true);
    try {
      setError(null);
      const data = await fetchTables();
      setTables(data);
      if (data.length > 0 && !selectedTable) {
        // Default to conversation_messages or the table with the highest row_count
        const topTbl =
          data.find((t) => t.name === "conversation_messages" && (t.row_count || 0) > 0) ||
          [...data].sort((a, b) => (b.row_count || 0) - (a.row_count || 0))[0] ||
          data[0];
        setSelectedTable(topTbl.name);
      }
    } catch (e: any) {
      console.error("Error loading tables:", e);
      setError(e?.message || "Failed to load database tables");
    } finally {
      setLoading(false);
    }
  }, [selectedTable]);

  const loadTableData = useCallback(async (tableName: string) => {
    if (!tableName) return;
    setTableLoading(true);
    try {
      setError(null);
      const data = await fetchTableData(tableName);
      const rawCols = data.columns || [];
      const normalizedCols: TableColumn[] = rawCols.map((c: any) => {
        if (typeof c === "string") {
          return { name: c, type: "text" };
        }
        return {
          name: c.name || String(c),
          type: c.type || "text",
        };
      });
      setColumns(normalizedCols);
      setRows(data.rows || []);
      setTotalRows(data.total_rows ?? data.total ?? (data.rows ? data.rows.length : 0));
    } catch (e: any) {
      console.error("Error loading table data:", e);
      setError(e?.message || `Failed to load table ${tableName}`);
    } finally {
      setTableLoading(false);
    }
  }, []);

  const handleClearTableData = async () => {
    if (!selectedTable) return;
    const confirmed = window.confirm(`⚠️ Are you sure you want to PERMANENTLY delete all data in table 'public.${selectedTable}'?`);
    if (!confirmed) return;

    setActionLoading(true);
    try {
      await clearTableData(selectedTable);
      alert(`✅ Table '${selectedTable}' cleared successfully!`);
      await loadTables();
      await loadTableData(selectedTable);
    } catch (err: any) {
      alert(`❌ Failed to clear table: ${err?.message || "Unknown error"}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleClearFullDatabase = async () => {
    const confirmed = window.confirm("🚨 DANGER: Are you sure you want to PERMANENTLY delete ALL DATA in ALL DATABASE TABLES?");
    if (!confirmed) return;

    const doubleCheck = window.prompt("Type 'DELETE' to confirm full database wipe:");
    if (doubleCheck !== "DELETE") {
      alert("Operation cancelled. Confirmation keyword did not match.");
      return;
    }

    setActionLoading(true);
    try {
      const result = await clearFullDatabase();
      alert(`✅ ${result.message || "Full database has been permanently cleared!"}`);
      await loadTables();
      if (selectedTable) {
        await loadTableData(selectedTable);
      }
    } catch (err: any) {
      alert(`❌ Failed to clear database: ${err?.message || "Unknown error"}`);
    } finally {
      setActionLoading(false);
    }
  };

  const handleDeleteRow = async (rowId: string | number) => {
    if (!selectedTable) return;
    if (!window.confirm(`Are you sure you want to delete row #${rowId} from table '${selectedTable}'?`)) {
      return;
    }

    setDeletingRowId(String(rowId));
    try {
      await deleteTableRow(selectedTable, rowId);
      await loadTables();
      await loadTableData(selectedTable);
    } catch (err: any) {
      alert(`❌ Failed to delete row: ${err?.message || "Unknown error"}`);
    } finally {
      setDeletingRowId(null);
    }
  };

  useEffect(() => {
    loadTables();
  }, [loadTables]);

  useEffect(() => {
    if (selectedTable) {
      loadTableData(selectedTable);
    }
  }, [selectedTable, loadTableData]);

  const filteredRows = rows.filter((r) => {
    if (!searchQuery) return true;
    if (!r) return false;
    const q = searchQuery.toLowerCase();
    const values = typeof r === "object" ? Object.values(r) : [r];
    return values.some((val) =>
      val !== null && val !== undefined && String(val).toLowerCase().includes(q)
    );
  });

  const hasIdColumn = columns.some((c) => c.name === "id");

  return (
    <div className="space-y-6 max-w-7xl mx-auto h-[calc(100vh-8rem)] flex flex-col">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 shrink-0">
        <div>
          <h1 className="text-xl font-bold text-gray-700 flex items-center gap-2">
            <Database className="h-5 w-5 text-[#088ADA]" />
            <span>PostgreSQL Database Explorer</span>
          </h1>
          <p className="text-xs text-gray-400 mt-0.5">
            Direct real-time inspection of durable tables, queue states, and audit records.
          </p>
        </div>

        <div className="flex items-center gap-2 self-start sm:self-auto">
          <button
            onClick={handleClearFullDatabase}
            disabled={actionLoading}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-rose-600 hover:bg-rose-700 text-white text-xs font-semibold shadow-sm transition disabled:opacity-50"
            title="Permanently delete all rows in all database tables"
          >
            <Trash2 className="h-3.5 w-3.5" />
            <span>Delete Full Database</span>
          </button>

          <button
            onClick={() => {
              loadTables();
              if (selectedTable) loadTableData(selectedTable);
            }}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-gray-100 hover:bg-gray-200 text-gray-500 text-xs font-medium border border-gray-200 transition"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${tableLoading || loading ? "animate-spin" : ""}`} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* Main Layout */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6 flex-1 min-h-0">
        {/* Left Column: Tables List */}
        <div className="bg-gray-50 border border-gray-200 rounded-xl flex flex-col overflow-hidden">
          <div className="p-3 border-b border-gray-200 bg-gray-100 text-xs font-semibold text-gray-500 flex items-center justify-between">
            <span className="flex items-center gap-1.5">
              <TableIcon className="h-3.5 w-3.5 text-[#088ADA]" />
              <span>Public Tables</span>
            </span>
            <span className="text-[11px] text-gray-400">{tables.length}</span>
          </div>

          <div className="flex-1 overflow-y-auto divide-y divide-gray-200/60 p-2 space-y-1">
            {tables.map((t) => {
              const isSelected = selectedTable === t.name;
              return (
                <button
                  key={t.name}
                  onClick={() => setSelectedTable(t.name)}
                  className={`w-full text-left px-3 py-2 rounded-lg transition text-xs flex items-center justify-between ${
                    isSelected
                      ? "bg-[#088ADA]/15 text-[#0778bd] border border-gray-300 font-semibold"
                      : "text-gray-400 hover:text-gray-600 hover:bg-gray-50"
                  }`}
                >
                  <span className="font-mono truncate">{t.name}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-100 font-mono text-gray-400">
                    {t.row_count}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Right Column: Table Viewer */}
        <div className="md:col-span-3 bg-gray-50 border border-gray-200 rounded-xl flex flex-col overflow-hidden">
          {/* Controls Bar */}
          <div className="p-3 border-b border-gray-200 bg-gray-100 flex flex-col sm:flex-row items-center justify-between gap-3 shrink-0">
            <div className="flex items-center gap-2">
              <span className="text-xs font-bold text-gray-600 font-mono">
                {selectedTable || "Select Table"}
              </span>
              <span className="text-[11px] text-gray-400 font-mono">
                ({filteredRows.length} of {totalRows} records)
              </span>
            </div>

            <div className="flex items-center gap-2 w-full sm:w-auto">
              <div className="relative w-full sm:w-64">
                <Search className="h-3.5 w-3.5 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  placeholder="Filter rows..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full pl-9 pr-3 py-1 bg-gray-50 border border-gray-200 rounded-lg text-xs text-gray-600 placeholder-gray-400 focus:outline-none focus:border-[#088ADA]"
                />
              </div>

              {selectedTable && (
                <button
                  onClick={handleClearTableData}
                  disabled={actionLoading}
                  className="flex items-center gap-1.5 px-3 py-1 bg-rose-50 hover:bg-rose-100 text-rose-700 border border-rose-200 rounded-lg text-xs font-semibold transition shrink-0 disabled:opacity-50"
                  title={`Permanently delete all rows in public.${selectedTable}`}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  <span>Clear Table Data</span>
                </button>
              )}
            </div>
          </div>

          {/* Table Data Matrix */}
          <div className="flex-1 overflow-auto min-h-0 text-xs font-mono">
            {error ? (
              <div className="p-8 text-center text-rose-500 bg-rose-950/20 m-4 rounded-xl border border-rose-900/40 space-y-3">
                <p className="text-xs font-semibold">{error}</p>
                <button
                  onClick={() => {
                    setError(null);
                    loadTables();
                    if (selectedTable) loadTableData(selectedTable);
                  }}
                  className="px-3 py-1.5 bg-gray-100 hover:bg-gray-200 text-xs text-gray-600 rounded border border-gray-200 transition font-sans"
                >
                  Retry
                </button>
              </div>
            ) : tableLoading ? (
              <div className="p-12 text-center text-gray-400 flex items-center justify-center gap-2">
                <RefreshCw className="h-4 w-4 animate-spin text-[#088ADA]" />
                <span>Loading table records...</span>
              </div>
            ) : columns.length === 0 ? (
              <div className="p-12 text-center text-gray-400">
                No data available for this table.
              </div>
            ) : (
              <table className="w-full text-left border-collapse">
                <thead className="bg-[#088ADA] text-white text-[11px] uppercase tracking-wider sticky top-0 border-b border-gray-300 z-20 shadow-sm">
                  <tr className="bg-[#088ADA]">
                    {columns.map((col) => (
                      <th key={col.name} className="p-3 font-bold whitespace-nowrap bg-[#088ADA] text-white">
                        <div className="flex items-center gap-1.5">
                          <span className="text-white">
                            {col.name.toLowerCase() === "workspace_name" ? "Slack Workspace" : col.name}
                          </span>
                          <span className="text-[10px] text-gray-200 font-normal lowercase">({col.type})</span>
                        </div>
                      </th>
                    ))}
                    {hasIdColumn && (
                      <th className="p-3 font-bold whitespace-nowrap bg-[#088ADA] text-white text-right">
                        Actions
                      </th>
                    )}
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-200">
                  {filteredRows.map((row, rIdx) => {
                    const rowId = Array.isArray(row) ? row[0] : (row ? row.id : undefined);
                    return (
                      <tr key={rIdx} className={`transition hover:bg-gray-200 ${rIdx % 2 === 0 ? "bg-white" : "bg-[#ededed]"}`}>
                        {columns.map((col, cIdx) => {
                          const rawVal = Array.isArray(row) ? row[cIdx] : (row ? row[col.name] : undefined);
                          const { isObj, text, full } = formatCellValue(rawVal, col.name);
                          const isWsId = col.name.toLowerCase() === "workspace_id";
                          const isWsName = col.name.toLowerCase() === "workspace_name";
                          return (
                            <td
                              key={`${col.name}-${cIdx}`}
                              className={`p-3 max-w-xs truncate text-[11px] ${
                                isWsId || isWsName ? "bg-blue-50/50 font-semibold text-gray-900" : "text-gray-700"
                              }`}
                              title={full}
                            >
                              {isObj ? (
                                <span className="px-1.5 py-0.5 rounded bg-gray-100 text-[#0778bd] border border-gray-300 text-[10px]">
                                  {text}
                                </span>
                              ) : isWsId ? (
                                <span className="px-2 py-0.5 rounded bg-blue-100 text-blue-800 font-mono font-bold text-[10px] border border-blue-200">
                                  {rawVal || "T01..."}
                                </span>
                              ) : isWsName ? (
                                <span className="font-bold text-gray-900 text-[11px]">
                                  {rawVal || "Axcel World"}
                                </span>
                              ) : rawVal === null || rawVal === undefined ? (
                                <span className="text-gray-400 font-normal">-</span>
                              ) : (
                                <span>{text}</span>
                              )}
                            </td>
                          );
                        })}
                        {hasIdColumn && (
                          <td className="p-3 text-right whitespace-nowrap">
                            <button
                              onClick={() => handleDeleteRow(rowId)}
                              disabled={deletingRowId === String(rowId)}
                              className="px-2 py-0.5 bg-rose-100 hover:bg-rose-200 text-rose-700 rounded text-[11px] font-semibold transition border border-rose-200 inline-flex items-center gap-1 disabled:opacity-50"
                              title={`Delete row #${rowId}`}
                            >
                              <Trash2 className="h-3 w-3" />
                              <span>Delete</span>
                            </button>
                          </td>
                        )}
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

