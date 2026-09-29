"use client";

import { useEffect, useMemo, useState } from "react";
import { ArrowDown, ArrowUp, ArrowUpDown, ChevronLeft, ChevronRight, Search } from "lucide-react";

export type Column<T> = {
  key: string;
  header: React.ReactNode;
  /** Cell content. Defaults to row[key]. */
  render?: (row: T, index: number) => React.ReactNode;
  /** Value used for sorting. Defaults to row[key]. */
  sortValue?: (row: T) => string | number | null | undefined;
  /** Text matched by the search box. Defaults to the sort value. */
  searchValue?: (row: T) => string;
  sortable?: boolean;
  align?: "left" | "right" | "center";
  className?: string;
  headerClassName?: string;
};

type SortState = { key: string; dir: "asc" | "desc" } | null;

const PAGE_SIZES = [10, 25, 50, 100];

function rawValue<T>(row: T, col: Column<T>): string | number | null | undefined {
  if (col.sortValue) return col.sortValue(row);
  const v = (row as Record<string, unknown>)[col.key];
  if (v === null || v === undefined) return v as null | undefined;
  if (typeof v === "number" || typeof v === "string") return v;
  return String(v);
}

function compare(a: string | number | null | undefined, b: string | number | null | undefined) {
  const aEmpty = a === null || a === undefined || a === "";
  const bEmpty = b === null || b === undefined || b === "";
  if (aEmpty && bEmpty) return 0;
  if (aEmpty) return 1;
  if (bEmpty) return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

function pageList(current: number, total: number): (number | "…")[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const pages = new Set([1, total, current - 1, current, current + 1]);
  const sorted = Array.from(pages).filter((p) => p >= 1 && p <= total).sort((a, b) => a - b);
  const out: (number | "…")[] = [];
  sorted.forEach((p, i) => {
    if (i > 0 && p - (sorted[i - 1] as number) > 1) out.push("…");
    out.push(p);
  });
  return out;
}

export function DataTable<T>({
  rows,
  columns,
  rowKey,
  searchable = true,
  searchPlaceholder = "Search...",
  initialSort = null,
  initialPageSize = 25,
  emptyMessage = "Nothing to show yet.",
  noMatchMessage = "No results match your search.",
  toolbar,
  rowClassName,
  onRowClick,
  itemLabel = "entries",
  overflowVisible = false,
}: {
  rows: T[];
  columns: Column<T>[];
  rowKey: (row: T, index: number) => string | number;
  searchable?: boolean;
  searchPlaceholder?: string;
  initialSort?: SortState;
  initialPageSize?: number;
  emptyMessage?: React.ReactNode;
  noMatchMessage?: React.ReactNode;
  /** Extra controls shown next to the search box (filters, buttons). */
  toolbar?: React.ReactNode;
  rowClassName?: (row: T, index: number) => string;
  onRowClick?: (row: T) => void;
  itemLabel?: string;
  /** Let dropdowns inside cells spill outside the table (disables horizontal scroll). */
  overflowVisible?: boolean;
}) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<SortState>(initialSort);
  const [pageSize, setPageSize] = useState(initialPageSize);
  const [page, setPage] = useState(1);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((row) =>
      columns.some((col) => {
        const v = col.searchValue ? col.searchValue(row) : rawValue(row, col);
        return v !== null && v !== undefined && String(v).toLowerCase().includes(q);
      })
    );
  }, [rows, columns, query]);

  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const col = columns.find((c) => c.key === sort.key);
    if (!col) return filtered;
    const copy = [...filtered];
    copy.sort((a, b) => {
      const r = compare(rawValue(a, col), rawValue(b, col));
      return sort.dir === "asc" ? r : -r;
    });
    return copy;
  }, [filtered, sort, columns]);

  const totalPages = Math.max(1, Math.ceil(sorted.length / pageSize));

  useEffect(() => {
    setPage(1);
  }, [query, pageSize, sort]);

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  const start = (page - 1) * pageSize;
  const visible = sorted.slice(start, start + pageSize);

  const toggleSort = (key: string) => {
    setSort((s) => {
      if (!s || s.key !== key) return { key, dir: "asc" };
      if (s.dir === "asc") return { key, dir: "desc" };
      return null;
    });
  };

  const alignClass = (a?: "left" | "right" | "center") =>
    a === "right" ? "text-right" : a === "center" ? "text-center" : "text-left";

  return (
    <div className="flex flex-col">
      {(searchable || toolbar) && (
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3 border-b border-gray-200 bg-white">
          {searchable ? (
            <div className="relative w-full sm:max-w-xs">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={searchPlaceholder}
                aria-label="Search table"
                className="w-full pl-9 pr-3 py-1.5 bg-white border border-gray-300 rounded-lg text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:ring-2 focus:ring-[#088ADA]/20 transition"
              />
            </div>
          ) : (
            <span />
          )}
          {toolbar && <div className="flex items-center gap-2 flex-wrap">{toolbar}</div>}
        </div>
      )}

      <div className={overflowVisible ? "overflow-visible" : "overflow-x-auto"}>
        <table className="w-full text-left text-xs">
          <thead className="bg-gray-50 border-b border-gray-200 text-gray-500 uppercase font-semibold text-[11px] tracking-wider">
            <tr>
              {columns.map((col) => {
                const sortable = col.sortable !== false;
                const active = sort?.key === col.key;
                const Icon = !active ? ArrowUpDown : sort!.dir === "asc" ? ArrowUp : ArrowDown;
                return (
                  <th
                    key={col.key}
                    className={`p-3 whitespace-nowrap ${alignClass(col.align)} ${col.headerClassName || ""}`}
                    aria-sort={active ? (sort!.dir === "asc" ? "ascending" : "descending") : undefined}
                  >
                    {sortable ? (
                      <button
                        type="button"
                        onClick={() => toggleSort(col.key)}
                        className={`inline-flex items-center gap-1 uppercase tracking-wider hover:text-gray-800 transition ${
                          active ? "text-gray-800" : ""
                        }`}
                        title="Click to sort"
                      >
                        {col.header}
                        <Icon className={`h-3 w-3 ${active ? "text-[#088ADA]" : "opacity-40"}`} />
                      </button>
                    ) : (
                      col.header
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100 text-gray-700">
            {visible.length === 0 ? (
              <tr>
                <td colSpan={columns.length} className="p-10 text-center text-sm text-gray-500">
                  {rows.length === 0 ? emptyMessage : noMatchMessage}
                </td>
              </tr>
            ) : (
              visible.map((row, i) => (
                <tr
                  key={rowKey(row, start + i)}
                  onClick={onRowClick ? () => onRowClick(row) : undefined}
                  className={`hover:bg-gray-50/80 transition ${onRowClick ? "cursor-pointer" : ""} ${
                    rowClassName ? rowClassName(row, start + i) : ""
                  }`}
                >
                  {columns.map((col) => (
                    <td key={col.key} className={`p-3 align-middle ${alignClass(col.align)} ${col.className || ""}`}>
                      {col.render
                        ? col.render(row, start + i)
                        : String((row as Record<string, unknown>)[col.key] ?? "—")}
                    </td>
                  ))}
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {rows.length > 0 && (
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3 border-t border-gray-200 bg-white text-xs text-gray-600">
          <div className="flex items-center gap-3 flex-wrap">
            <span>
              {sorted.length === 0
                ? `No matching ${itemLabel}`
                : `Showing ${start + 1}–${Math.min(start + pageSize, sorted.length)} of ${sorted.length.toLocaleString()} ${itemLabel}`}
              {sorted.length !== rows.length && ` (filtered from ${rows.length.toLocaleString()})`}
            </span>
            <label className="flex items-center gap-1.5">
              <span>Show</span>
              <select
                value={pageSize}
                onChange={(e) => setPageSize(Number(e.target.value))}
                className="px-2 py-1 border border-gray-300 rounded-md bg-white text-gray-800 focus:outline-none focus:border-[#088ADA]"
              >
                {PAGE_SIZES.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
              <span>per page</span>
            </label>
          </div>

          {totalPages > 1 && (
            <nav className="flex items-center gap-1" aria-label="Pagination">
              <button
                type="button"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={page === 1}
                className="p-1.5 rounded-md border border-gray-200 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
                aria-label="Previous page"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              {pageList(page, totalPages).map((p, i) =>
                p === "…" ? (
                  <span key={`gap-${i}`} className="px-1.5 text-gray-400">
                    …
                  </span>
                ) : (
                  <button
                    key={p}
                    type="button"
                    onClick={() => setPage(p)}
                    aria-current={p === page ? "page" : undefined}
                    className={`min-w-[28px] px-2 py-1 rounded-md border text-xs font-medium transition ${
                      p === page
                        ? "bg-[#088ADA] border-[#088ADA] text-white"
                        : "border-gray-200 hover:bg-gray-50 text-gray-700"
                    }`}
                  >
                    {p}
                  </button>
                )
              )}
              <button
                type="button"
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={page === totalPages}
                className="p-1.5 rounded-md border border-gray-200 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
                aria-label="Next page"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </nav>
          )}
        </div>
      )}
    </div>
  );
}
