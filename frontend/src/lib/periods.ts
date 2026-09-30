import type { DateRange } from "@/lib/api";

/** Billing periods use UTC calendar days so every viewer (and the server) agrees on the same numbers. */
export type PeriodPreset = "this_week" | "last_week" | "this_month" | "last_month" | "last_30" | "all" | "custom";

export const PERIOD_OPTIONS: Array<{ value: PeriodPreset; label: string }> = [
  { value: "this_month", label: "This month" },
  { value: "last_month", label: "Last month" },
  { value: "this_week", label: "This week" },
  { value: "last_week", label: "Last week" },
  { value: "last_30", label: "Last 30 days" },
  { value: "all", label: "All time" },
  { value: "custom", label: "Custom dates" },
];

export function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function utcToday(): Date {
  const now = new Date();
  return new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate()));
}

function addDays(d: Date, days: number): Date {
  const copy = new Date(d);
  copy.setUTCDate(copy.getUTCDate() + days);
  return copy;
}

/** Monday of the week containing d (UTC). */
function startOfWeek(d: Date): Date {
  const day = d.getUTCDay(); // 0 = Sunday
  return addDays(d, day === 0 ? -6 : 1 - day);
}

export function monthRange(year: number, monthIndex: number): { start: string; end: string } {
  const start = new Date(Date.UTC(year, monthIndex, 1));
  const end = new Date(Date.UTC(year, monthIndex + 1, 0));
  return { start: isoDay(start), end: isoDay(end) };
}

export function lastMonthRange(): { start: string; end: string } {
  const t = utcToday();
  return monthRange(t.getUTCFullYear(), t.getUTCMonth() - 1);
}

export function presetToRange(preset: PeriodPreset, custom: DateRange = {}): DateRange {
  const today = utcToday();
  switch (preset) {
    case "this_week":
      return { start: isoDay(startOfWeek(today)), end: isoDay(today) };
    case "last_week": {
      const start = addDays(startOfWeek(today), -7);
      return { start: isoDay(start), end: isoDay(addDays(start, 6)) };
    }
    case "this_month":
      return { start: isoDay(new Date(Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), 1))), end: isoDay(today) };
    case "last_month":
      return lastMonthRange();
    case "last_30":
      return { start: isoDay(addDays(today, -29)), end: isoDay(today) };
    case "custom":
      return { start: custom.start || undefined, end: custom.end || undefined };
    case "all":
    default:
      return {};
  }
}

const fmt = (iso: string) =>
  new Date(`${iso}T00:00:00Z`).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });

export function describeRange(range: DateRange): string {
  if (!range.start && !range.end) return "All time";
  if (range.start && range.end) return range.start === range.end ? fmt(range.start) : `${fmt(range.start)} – ${fmt(range.end)}`;
  if (range.start) return `From ${fmt(range.start)}`;
  return `Until ${fmt(range.end as string)}`;
}

/** Money for people: cents normally, more precision for tiny amounts so they don't show as $0.00. */
export function formatUsd(value: number): string {
  const v = Number(value || 0);
  if (v !== 0 && Math.abs(v) < 0.01) return `$${v.toFixed(4)}`;
  return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
