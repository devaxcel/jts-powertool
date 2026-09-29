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
import { DataTable, Column } from "@/components/DataTable";
import { TableInfo, TableColumn, formatLocalDateTime } from "@/lib/types";
import { PageHeader, Alert, btn } from "@/components/ui";

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
    <div className="space-y-5 max-w-7xl mx-auto h-[calc(100vh-7rem)] flex flex-col">
      <div className="shrink-0">
        <PageHeader
          icon={Database}
          title="Database"
          description="Browse the records the system stores. For troubleshooting only."
          actions={
            <>
              <button
                onClick={() => {
                  loadTables();
                  if (selectedTable) loadTableData(selectedTable);
                }}
                className={btn.secondary}
              >
                <RefreshCw className={`h-3.5 w-3.5 ${tableLoading || loading ? "animate-spin" : ""}`} />
                <span>Refresh</span>
              </button>
              <button
                onClick={handleClearFullDatabase}
                disabled={actionLoading}
                className={btn.danger}
                title="Permanently delete all rows in all database tables"
              >
                <Trash2 className="h-3.5 w-3.5" />
                <span>Delete all data</span>
              </button>
            </>
          }
        />
      </div>

      <div className="shrink-0">
        <Alert type="warning">
          Deleting rows or tables here is permanent and cannot be undone. Only use this if you know what the data is for.
        </Alert>
      </div>

      {/* Main Layout */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-6 flex-1 min-h-0">
        {/* Left Column: Tables List */}
        <div className="bg-gray-50 border border-gray-200 rounded-xl flex flex-col overflow-hidden">
          <div className="p-3 border-b border-gray-200 bg-gray-100 text-xs font-semibold text-gray-500 flex items-center justify-between">
            <span className="flex items-center gap-1.5">
              <TableIcon className="h-3.5 w-3.5 text-[#088ADA]" />
              <span>Tables</span>
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
                showing {filteredRows.length} of {totalRows} rows
              </span>
            </div>

            <div className="flex items-center gap-2 w-full sm:w-auto">
              <div className="relative w-full sm:w-64">
                <Search className="h-3.5 w-3.5 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type="text"
                  placeholder="Search in this table"
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
                  <span>Empty this table</span>
                </button>
              )}
            </div>
          </div>

          {/* Table Data Matrix */}
          <div className="flex-1 overflow-auto min-h-0 text-xs font-mono">
            {error ? (
              <div className="p-8 text-center text-rose-700 bg-rose-50 m-4 rounded-xl border border-rose-200 space-y-3 font-sans">
                <p className="text-sm font-semibold">Couldn&apos;t load this table</p>
                <p className="text-xs">{error}</p>
                <button
                  onClick={() => {
                    setError(null);
                    loadTables();
                    if (selectedTable) loadTableData(selectedTable);
                  }}
                  className={btn.secondary}
                >
                  Try again
                </button>
              </div>
            ) : tableLoading ? (
              <div className="p-12 text-center text-gray-400 flex items-center justify-center gap-2">
                <RefreshCw className="h-4 w-4 animate-spin text-[#088ADA]" />
                <span className="font-sans">Loading rows...</span>
              </div>
            ) : columns.length === 0 ? (
              <div className="p-12 text-center text-gray-400">
                <span className="font-sans">Choose a table on the left to see its rows.</span>
              </div>
            ) : (
              <div className="bg-white">
                <DataTable
                  key={selectedTable}
                  rows={filteredRows}
                  rowKey={(_row, rIdx) => rIdx}
                  searchable={false}
                  itemLabel="rows"
                  emptyMessage={<span className="font-sans">This table is empty.</span>}
                  columns={[
                    ...columns.map((col, cIdx): Column<any> => {
                      const getVal = (row: any) => (Array.isArray(row) ? row[cIdx] : row ? row[col.name] : undefined);
                      const isWsId = col.name.toLowerCase() === "workspace_id";
                      const isWsName = col.name.toLowerCase() === "workspace_name";
                      return {
                        key: `${col.name}-${cIdx}`,
                        header: (
                          <span className="flex items-center gap-1.5">
                            <span>{isWsName ? "Slack Workspace" : col.name}</span>
                            <span className="text-[10px] text-gray-400 font-normal lowercase">({col.type})</span>
                          </span>
                        ),
                        sortValue: (row) => {
                          const v = getVal(row);
                          if (v === null || v === undefined) return null;
                          if (typeof v === "number") return v;
                          return typeof v === "object" ? JSON.stringify(v) : String(v);
                        },
                        className: `max-w-xs truncate text-[11px] font-mono ${
                          isWsId || isWsName ? "bg-blue-50/50 font-semibold text-gray-900" : "text-gray-700"
                        }`,
                        render: (row) => {
                          const rawVal = getVal(row);
                          const { isObj, text, full } = formatCellValue(rawVal, col.name);
                          return (
                            <span title={full}>
                              {isObj ? (
                                <span className="px-1.5 py-0.5 rounded bg-gray-100 text-[#0778bd] border border-gray-300 text-[10px]">
                                  {text}
                                </span>
                              ) : isWsId ? (
                                <span className="px-2 py-0.5 rounded bg-blue-100 text-blue-800 font-mono font-bold text-[10px] border border-blue-200">
                                  {rawVal || "—"}
                                </span>
                              ) : isWsName ? (
                                <span className="font-bold text-gray-900 text-[11px]">{rawVal || "—"}</span>
                              ) : rawVal === null || rawVal === undefined ? (
                                <span className="text-gray-400 font-normal">-</span>
                              ) : (
                                text
                              )}
                            </span>
                          );
                        },
                      };
                    }),
                    ...(hasIdColumn
                      ? [
                          {
                            key: "__actions",
                            header: "Actions",
                            sortable: false,
                            align: "right" as const,
                            searchValue: () => "",
                            className: "whitespace-nowrap",
                            render: (row: any) => {
                              const rowId = Array.isArray(row) ? row[0] : row ? row.id : undefined;
                              return (
                                <button
                                  onClick={() => handleDeleteRow(rowId)}
                                  disabled={deletingRowId === String(rowId)}
                                  className="px-2 py-0.5 bg-rose-50 hover:bg-rose-100 text-rose-700 rounded text-[11px] font-semibold font-sans transition border border-rose-200 inline-flex items-center gap-1 disabled:opacity-50"
                                  title={`Delete row #${rowId}`}
                                >
                                  <Trash2 className="h-3 w-3" />
                                  <span>Delete</span>
                                </button>
                              );
                            },
                          } as Column<any>,
                        ]
                      : []),
                  ]}
                />
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

