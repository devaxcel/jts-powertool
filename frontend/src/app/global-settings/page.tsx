"use client";

import { useEffect, useState, useMemo, useRef } from "react";
import {
  Globe,
  Clock,
  Save,
  RotateCcw,
  Laptop,
  Radio,
  Sliders,
  ChevronDown,
  Check,
  Loader2,
  GitBranch,
  CheckSquare,
  Wrench,
  Boxes,
} from "lucide-react";
import { fetchGlobalSettings, updateGlobalSettings, resetGlobalSettings } from "@/lib/api";
import { PageHeader, Alert, ComingSoonBadge, btn } from "@/components/ui";
import { formatLocalDateTime } from "@/lib/types";

const DATE_FORMAT_OPTIONS = [
  { value: "YYYY-MM-DD", label: "YYYY-MM-DD (2026-09-24)" },
  { value: "DD/MM/YYYY", label: "DD/MM/YYYY (24/09/2026)" },
  { value: "MM/DD/YYYY", label: "MM/DD/YYYY (09/24/2026)" },
  { value: "DD MMM YYYY", label: "DD MMM YYYY (24 Sep 2026)" },
];

interface TimezoneOption {
  value: string;
  label: string;
  offset: string;
  region: string;
}

const POPULAR_TIMEZONES: TimezoneOption[] = [
  { value: "UTC", label: "UTC (Coordinated Universal Time)", offset: "UTC+00:00", region: "Universal" },
  { value: "Asia/Karachi", label: "Karachi, Pakistan (PKT)", offset: "UTC+05:00", region: "Asia" },
  { value: "Asia/Dubai", label: "Dubai, UAE (GST)", offset: "UTC+04:00", region: "Middle East" },
  { value: "Asia/Riyadh", label: "Riyadh, Saudi Arabia (AST)", offset: "UTC+03:00", region: "Middle East" },
  { value: "Asia/Kolkata", label: "India Standard Time (IST)", offset: "UTC+05:30", region: "Asia" },
  { value: "Asia/Singapore", label: "Singapore (SGT)", offset: "UTC+08:00", region: "Asia" },
  { value: "Asia/Tokyo", label: "Tokyo, Japan (JST)", offset: "UTC+09:00", region: "Asia" },
  { value: "Europe/London", label: "London, UK (GMT / BST)", offset: "UTC+00:00", region: "Europe" },
  { value: "Europe/Berlin", label: "Berlin / Paris / Rome (CET)", offset: "UTC+01:00", region: "Europe" },
  { value: "Europe/Istanbul", label: "Istanbul, Turkey (TRT)", offset: "UTC+03:00", region: "Europe" },
  { value: "America/New_York", label: "New York, USA (EST / EDT)", offset: "UTC-05:00", region: "Americas" },
  { value: "America/Chicago", label: "Chicago, USA (CST / CDT)", offset: "UTC-06:00", region: "Americas" },
  { value: "America/Denver", label: "Denver, USA (MST / MDT)", offset: "UTC-07:00", region: "Americas" },
  { value: "America/Los_Angeles", label: "Los Angeles, USA (PST / PDT)", offset: "UTC-08:00", region: "Americas" },
  { value: "America/Toronto", label: "Toronto, Canada (EST / EDT)", offset: "UTC-05:00", region: "Americas" },
  { value: "America/Sao_Paulo", label: "São Paulo, Brazil (BRT)", offset: "UTC-03:00", region: "Americas" },
  { value: "Australia/Sydney", label: "Sydney, Australia (AEST)", offset: "UTC+10:00", region: "Pacific" },
  { value: "Pacific/Auckland", label: "Auckland, New Zealand (NZST)", offset: "UTC+12:00", region: "Pacific" },
];

export default function GlobalSettingsPage() {
  const [selectedTimezone, setSelectedTimezone] = useState("UTC");
  const [dateFormat, setDateFormat] = useState("YYYY-MM-DD");
  const [timeFormat, setTimeFormat] = useState<"12h" | "24h">("12h");
  const [showSeconds, setShowSeconds] = useState(true);
  const [syncAlerts, setSyncAlerts] = useState(true);
  const [autoDST, setAutoDST] = useState(true);

  // Global Tool Default Action Permissions State
  const [globalGithubRead, setGlobalGithubRead] = useState(true);
  const [globalGithubPush, setGlobalGithubPush] = useState(true);
  const [globalJiraCreate, setGlobalJiraCreate] = useState(true);
  const [globalJiraClose, setGlobalJiraClose] = useState(false);

  const [currentTime, setCurrentTime] = useState<Date>(new Date());
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [searchFilter, setSearchFilter] = useState("");

  const [isDateFormatOpen, setIsDateFormatOpen] = useState(false);
  const dateFormatDropdownRef = useRef<HTMLDivElement>(null);

  const [isTzOpen, setIsTzOpen] = useState(false);
  const tzDropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (dateFormatDropdownRef.current && !dateFormatDropdownRef.current.contains(event.target as Node)) {
        setIsDateFormatOpen(false);
      }
      if (tzDropdownRef.current && !tzDropdownRef.current.contains(event.target as Node)) {
        setIsTzOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const selectedDateFormatOption = useMemo(() => {
    return DATE_FORMAT_OPTIONS.find((opt) => opt.value === dateFormat) || {
      value: dateFormat,
      label: dateFormat,
    };
  }, [dateFormat]);

  const selectedTzOption = useMemo(() => {
    return (
      POPULAR_TIMEZONES.find((tz) => tz.value === selectedTimezone) || {
        value: selectedTimezone,
        label: selectedTimezone,
        offset: "UTC",
        region: "Universal",
      }
    );
  }, [selectedTimezone]);

  // Load saved preferences from Backend API with local cache fallback
  useEffect(() => {
    async function loadGlobalSettings() {
      setLoading(true);
      try {
        const data = await fetchGlobalSettings();
        if (data) {
          if (data.timezone) setSelectedTimezone(data.timezone);
          if (data.date_format) setDateFormat(data.date_format);
          if (data.time_format) setTimeFormat(data.time_format as "12h" | "24h");
          if (data.show_seconds !== undefined) setShowSeconds(Boolean(data.show_seconds));
          if (data.sync_alerts !== undefined) setSyncAlerts(Boolean(data.sync_alerts));
          if (data.auto_dst !== undefined) setAutoDST(Boolean(data.auto_dst));

          if (typeof window !== "undefined") {
            localStorage.setItem("jts_global_timezone", data.timezone);
            localStorage.setItem("jts_date_format", data.date_format);
            localStorage.setItem("jts_time_format", data.time_format);
            localStorage.setItem("jts_show_seconds", String(data.show_seconds));
            localStorage.setItem("jts_sync_alerts", String(data.sync_alerts));
            sessionStorage.setItem("jts_global_timezone", data.timezone);
          }
        }
      } catch (err: any) {
        console.warn("Failed to fetch settings from backend, falling back to local storage:", err);
        if (typeof window !== "undefined") {
          const savedTz = localStorage.getItem("jts_global_timezone") || sessionStorage.getItem("jts_global_timezone");
          const savedDateFormat = localStorage.getItem("jts_date_format");
          const savedTimeFormat = localStorage.getItem("jts_time_format") as "12h" | "24h" | null;
          const savedShowSeconds = localStorage.getItem("jts_show_seconds");
          const savedSyncAlerts = localStorage.getItem("jts_sync_alerts");

          if (savedTz) setSelectedTimezone(savedTz);
          if (savedDateFormat) setDateFormat(savedDateFormat);
          if (savedTimeFormat) setTimeFormat(savedTimeFormat);
          if (savedShowSeconds !== null) setShowSeconds(savedShowSeconds === "true");
          if (savedSyncAlerts !== null) setSyncAlerts(savedSyncAlerts === "true");
        }
      } finally {
        if (typeof window !== "undefined") {
          const ghRead = localStorage.getItem("jts_global_github_read");
          const ghPush = localStorage.getItem("jts_global_github_push");
          const jrCreate = localStorage.getItem("jts_global_jira_create");
          const jrClose = localStorage.getItem("jts_global_jira_close");

          if (ghRead !== null) setGlobalGithubRead(ghRead === "true");
          if (ghPush !== null) setGlobalGithubPush(ghPush === "true");
          if (jrCreate !== null) setGlobalJiraCreate(jrCreate === "true");
          if (jrClose !== null) setGlobalJiraClose(jrClose === "true");
        }
        setLoading(false);
      }
    }

    loadGlobalSettings();
  }, []);

  // Update live clock every second
  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  // Format date helper with chosen timezone and formatting
  const formattedTimezoneTime = useMemo(() => {
    return formatLocalDateTime(currentTime, {
      timezone: selectedTimezone,
      dateFormat,
      timeFormat,
      showSeconds,
    });
  }, [currentTime, selectedTimezone, dateFormat, timeFormat, showSeconds]);

  const formattedUtcTime = useMemo(() => {
    return new Intl.DateTimeFormat("en-US", {
      timeZone: "UTC",
      year: "numeric",
      month: "short",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    }).format(currentTime) + " UTC";
  }, [currentTime]);

  const browserTimezone = useMemo(() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch {
      return "UTC";
    }
  }, []);

  const handleUseBrowserTz = () => {
    setSelectedTimezone(browserTimezone);
    setFeedback({
      type: "success",
      message: `Detected and selected browser timezone: ${browserTimezone}. Click 'Save Global Settings' to persist.`,
    });
  };

  const handleResetDefaults = async () => {
    setSaving(true);
    setFeedback(null);
    try {
      const data = await resetGlobalSettings();
      setSelectedTimezone(data.timezone || "UTC");
      setDateFormat(data.date_format || "YYYY-MM-DD");
      setTimeFormat((data.time_format as "12h" | "24h") || "12h");
      setShowSeconds(data.show_seconds !== undefined ? data.show_seconds : true);
      setSyncAlerts(data.sync_alerts !== undefined ? data.sync_alerts : true);
      setAutoDST(data.auto_dst !== undefined ? data.auto_dst : true);

      setGlobalGithubRead(true);
      setGlobalGithubPush(true);
      setGlobalJiraCreate(true);
      setGlobalJiraClose(false);

      if (typeof window !== "undefined") {
        localStorage.setItem("jts_global_timezone", "UTC");
        localStorage.setItem("jts_date_format", "YYYY-MM-DD");
        localStorage.setItem("jts_time_format", "12h");
        localStorage.setItem("jts_show_seconds", "true");
        localStorage.setItem("jts_sync_alerts", "true");
        localStorage.setItem("jts_global_github_read", "true");
        localStorage.setItem("jts_global_github_push", "true");
        localStorage.setItem("jts_global_jira_create", "true");
        localStorage.setItem("jts_global_jira_close", "false");
        sessionStorage.setItem("jts_global_timezone", "UTC");
        window.dispatchEvent(new Event("storage"));
      }

      setFeedback({
        type: "success",
        message: "Reset all global settings and tool default permissions to system defaults successfully.",
      });
    } catch (err: any) {
      setFeedback({
        type: "error",
        message: err.message || "Failed to reset global settings on server.",
      });
    } finally {
      setSaving(false);
    }
  };

  const handleSaveSettings = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setFeedback(null);
    try {
      const data = await updateGlobalSettings({
        timezone: selectedTimezone,
        date_format: dateFormat,
        time_format: timeFormat,
        show_seconds: showSeconds,
        auto_dst: autoDST,
        sync_alerts: syncAlerts,
      });

      if (typeof window !== "undefined") {
        localStorage.setItem("jts_global_timezone", data.timezone || selectedTimezone);
        localStorage.setItem("jts_date_format", data.date_format || dateFormat);
        localStorage.setItem("jts_time_format", data.time_format || timeFormat);
        localStorage.setItem("jts_show_seconds", String(data.show_seconds !== undefined ? data.show_seconds : showSeconds));
        localStorage.setItem("jts_sync_alerts", String(data.sync_alerts !== undefined ? data.sync_alerts : syncAlerts));
        localStorage.setItem("jts_global_github_read", String(globalGithubRead));
        localStorage.setItem("jts_global_github_push", String(globalGithubPush));
        localStorage.setItem("jts_global_jira_create", String(globalJiraCreate));
        localStorage.setItem("jts_global_jira_close", String(globalJiraClose));
        sessionStorage.setItem("jts_global_timezone", data.timezone || selectedTimezone);
        window.dispatchEvent(new Event("storage"));
      }

      setFeedback({
        type: "success",
        message: `Universal settings and tool permissions saved: timezone=${data.timezone || selectedTimezone}, format=${data.date_format || dateFormat} (${data.time_format || timeFormat}).`,
      });
    } catch (err: any) {
      setFeedback({
        type: "error",
        message: err.message || "Failed to persist global settings to backend database.",
      });
    } finally {
      setSaving(false);
    }
  };

  const filteredTimezones = useMemo(() => {
    if (!searchFilter.trim()) return POPULAR_TIMEZONES;
    const q = searchFilter.toLowerCase();
    return POPULAR_TIMEZONES.filter(
      (tz) =>
        tz.label.toLowerCase().includes(q) ||
        tz.value.toLowerCase().includes(q) ||
        tz.offset.toLowerCase().includes(q) ||
        tz.region.toLowerCase().includes(q)
    );
  }, [searchFilter]);

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <div className="flex flex-col items-center gap-3">
          <Loader2 className="h-8 w-8 text-[#088ADA] animate-spin" />
          <p className="text-xs text-gray-500 font-medium">Loading global system settings...</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-5xl mx-auto pb-12">
      <PageHeader
        icon={Globe}
        title="System settings"
        description="Default time zone and time format for everyone using the dashboard."
        badge={<span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-sky-50 text-[#088ADA] border border-sky-200">Applies to all users</span>}
        actions={
          <button type="button" onClick={handleUseBrowserTz} className={btn.secondary}>
            <Laptop className="h-3.5 w-3.5" />
            <span>Use my computer&apos;s time zone</span>
          </button>
        }
      />

      {feedback && <Alert type={feedback.type}>{feedback.message}</Alert>}

      {/* Live Clocks Bar */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm space-y-1">
          <div className="flex items-center justify-between text-xs text-gray-500 font-medium">
            <span className="flex items-center gap-1.5">
              <Clock className="h-3.5 w-3.5 text-[#088ADA]" />
              Dashboard time
            </span>
            <span className="text-[10px] px-1.5 py-0.5 bg-blue-50 text-[#088ADA] rounded font-mono font-semibold">
              IN USE
            </span>
          </div>
          <p className="text-lg font-bold text-gray-800 font-mono tracking-tight">
            {formattedTimezoneTime}
          </p>
          <p className="text-[11px] text-gray-400 truncate">{selectedTimezone}</p>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm space-y-1">
          <div className="flex items-center justify-between text-xs text-gray-500 font-medium">
            <span className="flex items-center gap-1.5">
              <Laptop className="h-3.5 w-3.5 text-emerald-500" />
              Your computer
            </span>
            <span className="text-[10px] px-1.5 py-0.5 bg-emerald-50 text-emerald-600 rounded font-mono">
              BROWSER
            </span>
          </div>
          <p className="text-lg font-bold text-gray-800 font-mono tracking-tight">
            {currentTime.toLocaleTimeString([], { hour12: timeFormat === "12h" })}
          </p>
          <p className="text-[11px] text-gray-400 truncate">{browserTimezone}</p>
        </div>

        <div className="bg-white border border-gray-200 rounded-xl p-4 shadow-sm space-y-1">
          <div className="flex items-center justify-between text-xs text-gray-500 font-medium">
            <span className="flex items-center gap-1.5">
              <Radio className="h-3.5 w-3.5 text-purple-500" />
              UTC (server time)
            </span>
            <span className="text-[10px] px-1.5 py-0.5 bg-purple-50 text-purple-600 rounded font-mono">
              BASE
            </span>
          </div>
          <p className="text-lg font-bold text-gray-800 font-mono tracking-tight">
            {formattedUtcTime.split(" ")[0]} {formattedUtcTime.split(" ")[1]}
          </p>
          <p className="text-[11px] text-gray-400 truncate">UTC+00:00 (Universal Reference)</p>
        </div>
      </div>

      <form onSubmit={handleSaveSettings} className="space-y-6">
        {/* Timezone Selection Card */}
        <div className="bg-white border border-gray-200 rounded-2xl p-6 space-y-4 shadow-sm">
          <div className="border-b border-gray-100 pb-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-bold text-gray-800 flex items-center gap-2">
                <Globe className="h-4 w-4 text-[#088ADA]" />
                <span>Default time zone</span>
              </h2>
              <p className="text-xs text-gray-500 mt-0.5">
                Dates and times in logs, approvals and conversations are shown in this time zone. Users can pick their own in My profile.
              </p>
            </div>

            <button
              type="button"
              onClick={handleUseBrowserTz}
              className="inline-flex items-center gap-1.5 text-xs font-medium text-[#088ADA] hover:text-[#0779bf] hover:underline transition self-start sm:self-auto"
            >
              <Laptop className="h-3.5 w-3.5" />
              <span>Detect Device Timezone ({browserTimezone})</span>
            </button>
          </div>

          <div className="relative" ref={tzDropdownRef}>
            <label className="text-xs font-semibold text-gray-700 block mb-1.5">
              Select Timezone
            </label>
            <button
              type="button"
              onClick={() => setIsTzOpen(!isTzOpen)}
              className="w-full flex items-center justify-between px-4 py-2.5 rounded-xl border border-gray-200 bg-white text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-[#088ADA] focus:border-transparent transition cursor-pointer font-medium hover:border-gray-300 shadow-sm"
            >
              <div className="flex items-center gap-2 truncate">
                <Clock className="h-4 w-4 text-[#088ADA] shrink-0" />
                <span className="font-semibold text-gray-800">{selectedTzOption.label}</span>
                <span className="text-xs font-mono px-2 py-0.5 bg-blue-50 text-[#088ADA] rounded-md font-medium">
                  {selectedTzOption.offset}
                </span>
                <span className="text-xs text-gray-400 font-mono hidden sm:inline">({selectedTzOption.value})</span>
              </div>
              <ChevronDown
                className={`h-4 w-4 text-gray-500 shrink-0 ml-2 transition-transform duration-200 ${
                  isTzOpen ? "rotate-180 text-[#088ADA]" : ""
                }`}
              />
            </button>

            {isTzOpen && (
              <div className="absolute z-50 mt-1.5 w-full bg-white border border-gray-200 rounded-xl shadow-xl overflow-hidden py-1">
                {/* Search Bar inside Dropdown */}
                <div className="p-2 border-b border-gray-100 bg-gray-50/50">
                  <input
                    type="text"
                    placeholder="Search region, country, or code..."
                    value={searchFilter}
                    onChange={(e) => setSearchFilter(e.target.value)}
                    onClick={(e) => e.stopPropagation()}
                    autoFocus
                    className="w-full px-3 py-1.5 bg-white border border-gray-200 rounded-lg text-xs text-gray-700 placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:ring-1 focus:ring-[#088ADA]"
                  />
                </div>

                <div className="max-h-64 overflow-y-auto py-1">
                  {filteredTimezones.length > 0 ? (
                    filteredTimezones.map((tz) => {
                      const isSelected = tz.value === selectedTimezone;
                      return (
                        <button
                          key={tz.value}
                          type="button"
                          onClick={() => {
                            setSelectedTimezone(tz.value);
                            setIsTzOpen(false);
                            setSearchFilter("");
                          }}
                          className={`w-full text-left px-4 py-2.5 text-sm flex items-center justify-between transition-colors duration-150 cursor-pointer ${
                            isSelected
                              ? "bg-[#088ADA] text-white font-medium"
                              : "text-gray-700 bg-white hover:bg-[#088ADA] hover:text-white"
                          }`}
                        >
                          <div className="flex flex-col truncate pr-2">
                            <span className="font-semibold text-xs leading-snug">{tz.label}</span>
                            <span
                              className={`text-[11px] font-mono leading-tight ${
                                isSelected ? "text-blue-100" : "text-gray-400 hover:text-white"
                              }`}
                            >
                              {tz.value}
                            </span>
                          </div>
                          <div className="flex items-center gap-2 shrink-0">
                            <span
                              className={`text-[11px] font-mono px-2 py-0.5 rounded ${
                                isSelected ? "bg-white/20 text-white" : "bg-gray-100 text-gray-600"
                              }`}
                            >
                              {tz.offset}
                            </span>
                            {isSelected && <Check className="h-4 w-4 text-white shrink-0 ml-1" />}
                          </div>
                        </button>
                      );
                    })
                  ) : (
                    <div className="px-4 py-6 text-center text-xs text-gray-400">
                      No timezones match &quot;{searchFilter}&quot;
                    </div>
                  )}

                  {!POPULAR_TIMEZONES.some((tz) => tz.value === selectedTimezone) && (
                    <button
                      type="button"
                      onClick={() => {
                        setIsTzOpen(false);
                        setSearchFilter("");
                      }}
                      className="w-full text-left px-4 py-2.5 text-sm flex items-center justify-between bg-[#088ADA] text-white font-medium cursor-pointer"
                    >
                      <div className="flex flex-col truncate pr-2">
                        <span className="font-semibold text-xs leading-snug">{selectedTimezone} (Custom / Device)</span>
                        <span className="text-[11px] font-mono text-blue-100 leading-tight">{selectedTimezone}</span>
                      </div>
                      <Check className="h-4 w-4 text-white shrink-0 ml-1" />
                    </button>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Display Formatting Preferences Card */}
        <div className="bg-white border border-gray-200 rounded-2xl p-6 space-y-5 shadow-sm">
          <div className="border-b border-gray-100 pb-4">
            <h2 className="text-sm font-bold text-gray-800 flex items-center gap-2">
              <Sliders className="h-4 w-4 text-[#088ADA]" />
              <span>Date and time format</span>
            </h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Choose how dates and times look across the dashboard.
            </p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-6">
            {/* Time Format */}
            <div className="space-y-2">
              <label className="text-xs font-semibold text-gray-700 block">
                Time Clock Format
              </label>
              <div className="grid grid-cols-2 gap-2">
                <button
                  type="button"
                  onClick={() => setTimeFormat("12h")}
                  className={`px-3 py-2 rounded-xl text-xs font-medium border transition flex items-center justify-center gap-2 ${
                    timeFormat === "12h"
                      ? "bg-[#088ADA] text-white border-[#088ADA] shadow-sm font-semibold"
                      : "bg-gray-50 hover:bg-gray-100 text-gray-700 border-gray-200"
                  }`}
                >
                  <span>12-Hour (03:30 PM)</span>
                </button>

                <button
                  type="button"
                  onClick={() => setTimeFormat("24h")}
                  className={`px-3 py-2 rounded-xl text-xs font-medium border transition flex items-center justify-center gap-2 ${
                    timeFormat === "24h"
                      ? "bg-[#088ADA] text-white border-[#088ADA] shadow-sm font-semibold"
                      : "bg-gray-50 hover:bg-gray-100 text-gray-700 border-gray-200"
                  }`}
                >
                  <span>24-Hour (15:30)</span>
                </button>
              </div>
            </div>

            {/* Date Format */}
            <div className="space-y-2">
              <label className="text-xs font-semibold text-gray-700 block">
                Date Display Format
              </label>
              <div className="relative" ref={dateFormatDropdownRef}>
                <button
                  type="button"
                  onClick={() => setIsDateFormatOpen(!isDateFormatOpen)}
                  className="w-full flex items-center justify-between px-3.5 py-2 bg-gray-50 border border-gray-200 rounded-xl text-xs text-gray-800 focus:outline-none focus:ring-2 focus:ring-[#088ADA] focus:border-transparent transition cursor-pointer font-medium hover:border-gray-300 shadow-sm"
                >
                  <span className="truncate">{selectedDateFormatOption.label}</span>
                  <ChevronDown className={`h-4 w-4 text-gray-500 shrink-0 ml-2 transition-transform duration-200 ${isDateFormatOpen ? "rotate-180 text-[#088ADA]" : ""}`} />
                </button>

                {isDateFormatOpen && (
                  <div className="absolute z-50 mt-1.5 w-full bg-white border border-gray-200 rounded-xl shadow-xl max-h-60 overflow-y-auto py-1">
                    {DATE_FORMAT_OPTIONS.map((opt) => {
                      const isSelected = opt.value === dateFormat;
                      return (
                        <button
                          key={opt.value}
                          type="button"
                          onClick={() => {
                            setDateFormat(opt.value);
                            setIsDateFormatOpen(false);
                          }}
                          className={`w-full text-left px-3.5 py-2.5 text-xs flex items-center justify-between transition-colors duration-150 cursor-pointer ${
                            isSelected
                              ? "bg-[#088ADA] text-white font-medium"
                              : "text-gray-700 bg-white hover:bg-[#088ADA] hover:text-white"
                          }`}
                        >
                          <span className="truncate">{opt.label}</span>
                          {isSelected && (
                            <Check className="h-3.5 w-3.5 text-white shrink-0 ml-2" />
                          )}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Toggle Switches */}
          <div className="pt-2 border-t border-gray-100 space-y-3">
            <label className="flex items-center justify-between cursor-pointer p-2 rounded-lg hover:bg-gray-50 transition">
              <div>
                <span className="text-xs font-semibold text-gray-800 block">Display Seconds in Timestamps</span>
                <span className="text-[11px] text-gray-400">Show exact seconds in all system logs and telemetry feeds.</span>
              </div>
              <input
                type="checkbox"
                checked={showSeconds}
                onChange={(e) => setShowSeconds(e.target.checked)}
                className="h-4 w-4 text-[#088ADA] rounded border-gray-300 focus:ring-[#088ADA]"
              />
            </label>

            <label className="flex items-center justify-between cursor-pointer p-2 rounded-lg hover:bg-gray-50 transition">
              <div>
                <span className="text-xs font-semibold text-gray-800 block">Daylight Saving Time (DST) Auto-Adjustment</span>
                <span className="text-[11px] text-gray-400">Automatically adjust offsets when regions transition to summer/winter time.</span>
              </div>
              <input
                type="checkbox"
                checked={autoDST}
                onChange={(e) => setAutoDST(e.target.checked)}
                className="h-4 w-4 text-[#088ADA] rounded border-gray-300 focus:ring-[#088ADA]"
              />
            </label>

            <label className="flex items-center justify-between cursor-pointer p-2 rounded-lg hover:bg-gray-50 transition">
              <div>
                <span className="text-xs font-semibold text-gray-800 block">Sync Timestamps with Slack & Telegram Alerts</span>
                <span className="text-[11px] text-gray-400">Attach chosen timezone tags to outgoing webhook notifications.</span>
              </div>
              <input
                type="checkbox"
                checked={syncAlerts}
                onChange={(e) => setSyncAlerts(e.target.checked)}
                className="h-4 w-4 text-[#088ADA] rounded border-gray-300 focus:ring-[#088ADA]"
              />
            </label>
          </div>
        </div>

        {/* Global Tools & Action Permissions Card */}
        <div className="bg-white border border-gray-200 rounded-2xl p-6 space-y-5 shadow-sm">
          <div className="border-b border-gray-100 pb-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-bold text-gray-800 flex items-center gap-2">
                <Wrench className="h-4 w-4 text-[#088ADA]" />
                <span>Tool permissions</span>
                <ComingSoonBadge />
              </h2>
              <p className="text-xs text-gray-500 mt-0.5">
                Preview only: these switches do not change what the AI is allowed to do yet.
              </p>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-[11px] font-semibold px-2.5 py-1 rounded-full bg-blue-50 text-[#088ADA] border border-blue-200 flex items-center gap-1.5">
                <Boxes className="h-3 w-3" />
                2 tools
              </span>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {/* GitHub Tool Card */}
            <div className="p-4 bg-gray-50/70 border border-gray-200 rounded-xl space-y-3.5 hover:border-gray-300 transition">
              <div className="flex items-center justify-between border-b border-gray-200/80 pb-2.5">
                <div className="flex items-center gap-2.5">
                  <div className="h-8 w-8 rounded-lg bg-gray-900 text-white flex items-center justify-center shrink-0">
                    <GitBranch className="h-4 w-4 text-white" />
                  </div>
                  <div>
                    <h3 className="text-xs font-bold text-gray-800">GitHub</h3>
                    <p className="text-[11px] text-gray-400">Repositories, code push &amp; PR review</p>
                  </div>
                </div>
                <span className="text-[10px] font-semibold px-2 py-0.5 rounded bg-emerald-50 text-emerald-700 border border-emerald-200">
                  Active
                </span>
              </div>

              <div className="space-y-2.5">
                <span className="text-[11px] font-semibold text-gray-600 block">Default Action Permissions:</span>
                
                <label className="flex items-start gap-2.5 cursor-pointer p-2.5 rounded-lg bg-white border border-gray-200 hover:border-[#088ADA]/50 transition select-none">
                  <input
                    type="checkbox"
                    checked={globalGithubRead}
                    onChange={(e) => setGlobalGithubRead(e.target.checked)}
                    className="rounded border-gray-300 text-[#088ADA] focus:ring-[#088ADA] h-4 w-4 mt-0.5"
                  />
                  <div>
                    <span className="text-xs font-semibold text-gray-800 block">Can Read Repos</span>
                    <span className="text-[11px] text-gray-500">Allow users to view repositories, branches, and code files.</span>
                  </div>
                </label>

                <label className="flex items-start gap-2.5 cursor-pointer p-2.5 rounded-lg bg-white border border-gray-200 hover:border-[#088ADA]/50 transition select-none">
                  <input
                    type="checkbox"
                    checked={globalGithubPush}
                    onChange={(e) => setGlobalGithubPush(e.target.checked)}
                    className="rounded border-gray-300 text-[#088ADA] focus:ring-[#088ADA] h-4 w-4 mt-0.5"
                  />
                  <div>
                    <span className="text-xs font-semibold text-gray-800 block">Can Push Code / Merge PRs</span>
                    <span className="text-[11px] text-gray-500">Allow users to commit code, push branch updates, and merge pull requests.</span>
                  </div>
                </label>
              </div>
            </div>

            {/* Jira Tool Card */}
            <div className="p-4 bg-gray-50/70 border border-gray-200 rounded-xl space-y-3.5 hover:border-gray-300 transition">
              <div className="flex items-center justify-between border-b border-gray-200/80 pb-2.5">
                <div className="flex items-center gap-2.5">
                  <div className="h-8 w-8 rounded-lg bg-purple-600 text-white flex items-center justify-center shrink-0">
                    <CheckSquare className="h-4 w-4 text-white" />
                  </div>
                  <div>
                    <h3 className="text-xs font-bold text-gray-800">Jira</h3>
                    <p className="text-[11px] text-gray-400">Issue tracking, tickets &amp; agile boards</p>
                  </div>
                </div>
                <span className="text-[10px] font-semibold px-2 py-0.5 rounded bg-purple-50 text-purple-700 border border-purple-200">
                  Future Integration
                </span>
              </div>

              <div className="space-y-2.5">
                <span className="text-[11px] font-semibold text-gray-600 block">Default Action Permissions:</span>
                
                <label className="flex items-start gap-2.5 cursor-pointer p-2.5 rounded-lg bg-white border border-gray-200 hover:border-purple-500/50 transition select-none">
                  <input
                    type="checkbox"
                    checked={globalJiraCreate}
                    onChange={(e) => setGlobalJiraCreate(e.target.checked)}
                    className="rounded border-gray-300 text-purple-600 focus:ring-purple-600 h-4 w-4 mt-0.5"
                  />
                  <div>
                    <span className="text-xs font-semibold text-gray-800 block">Can Create Tickets</span>
                    <span className="text-[11px] text-gray-500">Allow users to open new tasks, bugs, and user story items.</span>
                  </div>
                </label>

                <label className="flex items-start gap-2.5 cursor-pointer p-2.5 rounded-lg bg-white border border-gray-200 hover:border-purple-500/50 transition select-none">
                  <input
                    type="checkbox"
                    checked={globalJiraClose}
                    onChange={(e) => setGlobalJiraClose(e.target.checked)}
                    className="rounded border-gray-300 text-purple-600 focus:ring-purple-600 h-4 w-4 mt-0.5"
                  />
                  <div>
                    <span className="text-xs font-semibold text-gray-800 block">Can Close/Delete Tickets</span>
                    <span className="text-[11px] text-gray-500">Allow users to resolve, close, or delete tickets.</span>
                  </div>
                </label>
              </div>
            </div>
          </div>
        </div>

        {/* Action Buttons */}
        <div className="flex items-center justify-between pt-2">
          <button
            type="button"
            onClick={handleResetDefaults}
            disabled={saving}
            className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-gray-100 hover:bg-gray-200 text-gray-600 text-xs font-semibold transition disabled:opacity-50 cursor-pointer"
          >
            {saving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RotateCcw className="h-3.5 w-3.5" />}
            <span>Reset to Defaults</span>
          </button>

          <button
            type="submit"
            disabled={saving}
            className="flex items-center gap-2 px-6 py-2.5 rounded-xl bg-[#088ADA] hover:bg-[#0778bd] text-white text-xs font-semibold shadow-md transition disabled:opacity-50 cursor-pointer"
          >
            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save className="h-4 w-4" />}
            <span>{saving ? "Saving Settings..." : "Save Global Settings"}</span>
          </button>
        </div>
      </form>
    </div>
  );
}
