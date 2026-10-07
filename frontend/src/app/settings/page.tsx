"use client";

import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import {
  Settings,
  User,
  Mail,
  Lock,
  Eye,
  EyeOff,
  Shield,
  Loader2,
  Save,
  Globe,
  Clock,
  Laptop,
  ChevronDown,
  Check,
} from "lucide-react";
import { fetchMyProfile, updateMyProfile, fetchGlobalSettings } from "@/lib/api";
import { PageHeader, Alert, LoadingState } from "@/components/ui";
import { DashboardUser } from "@/lib/types";
import { MfaStatusCard } from "@/components/MfaStatusCard";

const TIMEZONE_OPTIONS = [
  { value: "UTC", label: "UTC (Coordinated Universal Time)", offset: "UTC+00:00" },
  { value: "Asia/Karachi", label: "Karachi, Pakistan (PKT)", offset: "UTC+05:00" },
  { value: "Asia/Dubai", label: "Dubai, UAE (GST)", offset: "UTC+04:00" },
  { value: "Asia/Riyadh", label: "Riyadh, Saudi Arabia (AST)", offset: "UTC+03:00" },
  { value: "Asia/Kolkata", label: "India Standard Time (IST)", offset: "UTC+05:30" },
  { value: "Asia/Dhaka", label: "Dhaka, Bangladesh (BST)", offset: "UTC+06:00" },
  { value: "Asia/Singapore", label: "Singapore (SGT)", offset: "UTC+08:00" },
  { value: "Asia/Tokyo", label: "Tokyo, Japan (JST)", offset: "UTC+09:00" },
  { value: "Europe/London", label: "London, UK (GMT / BST)", offset: "UTC+00:00" },
  { value: "Europe/Berlin", label: "Berlin / Paris / Rome (CET / CEST)", offset: "UTC+01:00" },
  { value: "Europe/Istanbul", label: "Istanbul, Turkey (TRT)", offset: "UTC+03:00" },
  { value: "America/New_York", label: "New York, USA (EST / EDT)", offset: "UTC-05:00" },
  { value: "America/Chicago", label: "Chicago, USA (CST / CDT)", offset: "UTC-06:00" },
  { value: "America/Denver", label: "Denver, USA (MST / MDT)", offset: "UTC-07:00" },
  { value: "America/Los_Angeles", label: "Los Angeles, USA (PST / PDT)", offset: "UTC-08:00" },
  { value: "America/Toronto", label: "Toronto, Canada (EST / EDT)", offset: "UTC-05:00" },
  { value: "America/Sao_Paulo", label: "São Paulo, Brazil (BRT)", offset: "UTC-03:00" },
  { value: "Australia/Sydney", label: "Sydney, Australia (AEST)", offset: "UTC+10:00" },
  { value: "Pacific/Auckland", label: "Auckland, New Zealand (NZST)", offset: "UTC+12:00" },
];

export default function SettingsPage() {
  const [profile, setProfile] = useState<DashboardUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [showPassword, setShowPassword] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  // Form State
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [timezone, setTimezone] = useState("SYSTEM");
  const [systemTimezone, setSystemTimezone] = useState("UTC");
  const [currentTime, setCurrentTime] = useState<Date>(new Date());

  // Load system global timezone
  useEffect(() => {
    if (typeof window !== "undefined") {
      const savedGlobal =
        localStorage.getItem("jts_global_timezone") ||
        sessionStorage.getItem("jts_global_timezone") ||
        "UTC";
      setSystemTimezone(savedGlobal);
    }
    fetchGlobalSettings()
      .then((gs) => {
        if (gs?.timezone) {
          setSystemTimezone(gs.timezone);
        }
      })
      .catch(() => {});
  }, []);

  // Update live clock
  useEffect(() => {
    const timer = setInterval(() => setCurrentTime(new Date()), 1000);
    return () => clearInterval(timer);
  }, []);

  const effectiveTimezone = useMemo(() => {
    if (!timezone || timezone === "SYSTEM" || timezone === "System Time") {
      return systemTimezone;
    }
    return timezone;
  }, [timezone, systemTimezone]);

  const formattedTzPreview = useMemo(() => {
    try {
      return new Intl.DateTimeFormat("en-US", {
        timeZone: effectiveTimezone,
        year: "numeric",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: true,
      }).format(currentTime);
    } catch {
      return currentTime.toUTCString();
    }
  }, [currentTime, effectiveTimezone]);

  const browserTimezone = useMemo(() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    } catch {
      return "UTC";
    }
  }, []);

  const [isTzOpen, setIsTzOpen] = useState(false);
  const tzDropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (tzDropdownRef.current && !tzDropdownRef.current.contains(event.target as Node)) {
        setIsTzOpen(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const systemTimezoneOffset = useMemo(() => {
    const match = TIMEZONE_OPTIONS.find((t) => t.value === systemTimezone);
    return match ? match.offset : "System Default";
  }, [systemTimezone]);

  const selectedTzOption = useMemo(() => {
    if (!timezone || timezone === "SYSTEM" || timezone === "System Time") {
      return {
        value: "SYSTEM",
        label: `System Time (Default: ${systemTimezone})`,
        offset: systemTimezoneOffset,
      };
    }
    return (
      TIMEZONE_OPTIONS.find((tz) => tz.value === timezone) || {
        value: timezone,
        label: timezone,
        offset: "Custom / Device",
      }
    );
  }, [timezone, systemTimezone, systemTimezoneOffset]);

  const handleSelectTimezone = (newTz: string) => {
    setTimezone(newTz);
    setIsTzOpen(false);
    if (typeof window !== "undefined") {
      sessionStorage.setItem("jts_user_timezone", newTz);
      localStorage.setItem("jts_user_timezone", newTz);
      document.cookie = `jts_user_tz=${encodeURIComponent(newTz)}; path=/; max-age=31536000; SameSite=Lax`;
      try {
        const raw = sessionStorage.getItem("jts_user");
        if (raw) {
          const u = JSON.parse(raw);
          u.timezone = newTz;
          sessionStorage.setItem("jts_user", JSON.stringify(u));
        }
      } catch {}
      window.dispatchEvent(new Event("storage"));
    }
  };

  const handleUseBrowserTz = () => {
    handleSelectTimezone(browserTimezone);
    setFeedback({
      type: "success",
      message: `Selected your device timezone (${browserTimezone}). Click 'Save Profile Settings' to apply.`,
    });
  };

  const loadProfile = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchMyProfile();
      setProfile(data);
      setName(data.name || data.username || "");
      setUsername(data.username || "");
      setEmail(data.email || "");
      const tz =
        data.timezone ||
        sessionStorage.getItem("jts_user_timezone") ||
        localStorage.getItem("jts_user_timezone") ||
        "SYSTEM";
      setTimezone(tz);
      setPassword("");
      if (typeof window !== "undefined" && tz) {
        sessionStorage.setItem("jts_user_timezone", tz);
      }
    } catch (err: any) {
      console.error("Failed to load profile:", err);
      // Fallback from sessionStorage if backend endpoint fails
      try {
        const raw = sessionStorage.getItem("jts_user");
        const savedTz =
          sessionStorage.getItem("jts_user_timezone") ||
          localStorage.getItem("jts_user_timezone");
        if (raw) {
          const u = JSON.parse(raw);
          setProfile(u);
          setName(u.name || u.username || "");
          setUsername(u.username || "");
          setEmail(u.email || "");
          setTimezone(u.timezone || savedTz || "SYSTEM");
        }
      } catch {}
      setFeedback({ type: "error", message: err?.message || "We could not load your profile. Please refresh the page." });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadProfile();
  }, [loadProfile]);

  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    setFeedback(null);

    const cleanName = name.trim();
    const cleanEmail = email.trim();
    const cleanPassword = password.trim();
    const cleanTimezone = timezone.trim() || "SYSTEM";

    if (!cleanName) {
      setFeedback({ type: "error", message: "Please enter your full name." });
      return;
    }

    if (cleanEmail && (!cleanEmail.includes("@") || !cleanEmail.includes("."))) {
      setFeedback({ type: "error", message: "Please enter a valid email address." });
      return;
    }

    setSaving(true);
    try {
      const resp = await updateMyProfile({
        name: cleanName,
        email: cleanEmail || undefined,
        password: cleanPassword || undefined,
        timezone: cleanTimezone,
      });

      const updated = resp.user || {
        ...profile,
        name: cleanName,
        email: cleanEmail,
        timezone: cleanTimezone,
      };

      setProfile(updated);
      setName(updated.name || cleanName);
      setEmail(updated.email || cleanEmail);
      const finalTz = updated.timezone || cleanTimezone;
      setTimezone(finalTz);
      setPassword("");

      // Update sessionStorage, localStorage, and cookies so all components immediately reflect the updated details and user timezone
      try {
        const rawUser = sessionStorage.getItem("jts_user");
        const existingObj = rawUser ? JSON.parse(rawUser) : {};
        const merged = { ...existingObj, ...updated, name: cleanName, timezone: finalTz };
        sessionStorage.setItem("jts_user", JSON.stringify(merged));
        sessionStorage.setItem("jts_user_timezone", finalTz);
        localStorage.setItem("jts_user_timezone", finalTz);
        document.cookie = `jts_user_tz=${encodeURIComponent(finalTz)}; path=/; max-age=31536000; SameSite=Lax`;
        window.dispatchEvent(new Event("storage"));
      } catch {}

      setFeedback({
        type: "success",
        message: "Your profile details and personal timezone preference have been saved successfully.",
      });
    } catch (err: any) {
      setFeedback({ type: "error", message: err?.message || "We could not save your changes. Please try again." });
    } finally {
      setSaving(false);
    }
  };

  const roleBadge = (r?: string) => {
    const norm = r || profile?.role || "jts_admin";
    if (norm === "jts_admin" || norm === "admin") {
      return (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-amber-500/10 text-amber-600 border border-amber-500/20">
          <Shield className="h-3.5 w-3.5" />
          JTS Master Admin
        </span>
      );
    }
    if (norm === "client_admin") {
      return (
        <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-blue-500/10 text-blue-600 border border-blue-500/20">
          <Shield className="h-3.5 w-3.5" />
          Client Admin
        </span>
      );
    }
    return (
      <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-semibold bg-gray-500/10 text-gray-600 border border-gray-500/20">
        <User className="h-3.5 w-3.5" />
        Client Standard
      </span>
    );
  };

  return (
    <div className="p-4 sm:p-6 lg:p-8 max-w-4xl mx-auto space-y-6">
      <PageHeader
        icon={Settings}
        title="My profile"
        description="Your name, email and password."
        badge={profile ? roleBadge(profile.role) : undefined}
      />

      {feedback && <Alert type={feedback.type}>{feedback.message}</Alert>}

      {loading ? (
        <LoadingState label="Loading your profile..." />
      ) : (
        <form onSubmit={handleSaveProfile} className="space-y-6">
          {/* Card 1: Personal Information */}
          <div className="bg-white rounded-2xl border border-gray-200/80 shadow-sm p-6 space-y-5">
            <div className="border-b border-gray-100 pb-3">
              <h2 className="text-sm font-semibold text-gray-800">Your details</h2>
              <p className="text-xs text-gray-400">
                This is how you appear to others and how you sign in.
              </p>
            </div>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
              {/* Full Name */}
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-gray-700 flex items-center gap-1.5">
                  <User className="h-3.5 w-3.5 text-gray-400" />
                  Full Name <span className="text-rose-500">*</span>
                </label>
                <input
                  type="text"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. John Doe"
                  className="w-full px-3.5 py-2 rounded-xl border border-gray-200 bg-white text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-[#088ADA] focus:border-transparent transition"
                />
                <p className="text-[11px] text-gray-400">
                  This full name is displayed across user management, audit logs, and secret creation.
                </p>
              </div>

              {/* Username / User ID (Readonly) */}
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-gray-700 flex items-center gap-1.5">
                  <Lock className="h-3.5 w-3.5 text-gray-400" />
                  Username / User ID <span className="text-gray-400 font-normal">(Read-only)</span>
                </label>
                <div className="relative">
                  <input
                    type="text"
                    disabled
                    value={username}
                    className="w-full px-3.5 py-2 rounded-xl border border-gray-200 bg-gray-50 text-sm text-gray-500 cursor-not-allowed font-mono"
                  />
                  <Lock className="h-4 w-4 text-gray-400 absolute right-3 top-2.5" />
                </div>
                <p className="text-[11px] text-gray-400">
                  Username is unique and permanent for authentication isolation.
                </p>
              </div>

              {/* Email Address */}
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-gray-700 flex items-center gap-1.5">
                  <Mail className="h-3.5 w-3.5 text-gray-400" />
                  Email Address <span className="text-rose-500">*</span>
                </label>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="e.g. user@company.com"
                  className="w-full px-3.5 py-2 rounded-xl border border-gray-200 bg-white text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-[#088ADA] focus:border-transparent transition"
                />
                <p className="text-[11px] text-gray-400">
                  Used for notifications and account password recovery. Must be unique.
                </p>
              </div>

              {/* Password */}
              <div className="space-y-1.5">
                <label className="text-xs font-semibold text-gray-700 flex items-center gap-1.5">
                  <Lock className="h-3.5 w-3.5 text-gray-400" />
                  Password <span className="text-gray-400 font-normal">(Leave blank to keep current)</span>
                </label>
                <div className="relative">
                  <input
                    type={showPassword ? "text" : "password"}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="Enter new password (optional)"
                    className="w-full px-3.5 py-2 pr-10 rounded-xl border border-gray-200 bg-white text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-[#088ADA] focus:border-transparent transition"
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword(!showPassword)}
                    className="absolute right-3 top-2.5 text-gray-400 hover:text-gray-600 transition"
                  >
                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
                <p className="text-[11px] text-gray-400">
                  Enter 6+ characters if you wish to change your login password.
                </p>
              </div>
            </div>
          </div>

          {/* Card 2: Personal Timezone & Display Preference */}
          <div className="bg-white rounded-2xl border border-gray-200/80 shadow-sm p-6 space-y-5">
            <div className="border-b border-gray-100 pb-3 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div>
                <h2 className="text-sm font-semibold text-gray-800 flex items-center gap-2">
                  <Globe className="h-4 w-4 text-[#088ADA]" />
                  Time zone
                </h2>
                <p className="text-xs text-gray-400">
                  All dates and times on the dashboard are shown in this time zone.
                </p>
              </div>
              <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-medium bg-cyan-50 text-cyan-700 border border-cyan-200">
                <Shield className="h-3 w-3" />
                Profile-Level Only
              </span>
            </div>

            <div className="space-y-4">
              {/* Dropdown & Quick Detect */}
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <label className="text-xs font-semibold text-gray-700 flex items-center gap-1.5">
                    <Clock className="h-3.5 w-3.5 text-gray-400" />
                    Your time zone
                  </label>
                  <button
                    type="button"
                    onClick={handleUseBrowserTz}
                    className="inline-flex items-center gap-1 text-[11px] font-medium text-[#088ADA] hover:text-[#0779bf] hover:underline transition"
                  >
                    <Laptop className="h-3 w-3" />
                    Detect Device Timezone ({browserTimezone})
                  </button>
                </div>
                <div className="relative" ref={tzDropdownRef}>
                  <button
                    type="button"
                    onClick={() => setIsTzOpen(!isTzOpen)}
                    className="w-full flex items-center justify-between px-3.5 py-2.5 rounded-xl border border-gray-200 bg-white text-sm text-gray-800 focus:outline-none focus:ring-2 focus:ring-[#088ADA] focus:border-transparent transition cursor-pointer font-medium hover:border-gray-300 shadow-sm"
                  >
                    <span className="truncate">
                      {selectedTzOption.label} &nbsp; ({selectedTzOption.offset})
                    </span>
                    <ChevronDown className={`h-4 w-4 text-gray-500 shrink-0 ml-2 transition-transform duration-200 ${isTzOpen ? "rotate-180 text-[#088ADA]" : ""}`} />
                  </button>

                  {isTzOpen && (
                    <div className="absolute z-50 mt-1.5 w-full bg-white border border-gray-200 rounded-xl shadow-xl max-h-64 overflow-y-auto py-1">
                      {/* System Time option at the very top */}
                      <button
                        type="button"
                        onClick={() => handleSelectTimezone("SYSTEM")}
                        className={`w-full text-left px-3.5 py-2.5 text-sm flex items-center justify-between border-b border-gray-100 transition-colors duration-150 cursor-pointer ${
                          timezone === "SYSTEM" || timezone === "System Time"
                            ? "bg-[#088ADA] text-white font-medium"
                            : "text-gray-700 bg-white hover:bg-[#088ADA] hover:text-white"
                        }`}
                      >
                        <div className="flex flex-col truncate pr-2">
                          <span className="font-semibold text-xs leading-snug">System Time</span>
                          <span
                            className={`text-[11px] font-mono leading-tight ${
                              timezone === "SYSTEM" || timezone === "System Time"
                                ? "text-blue-100"
                                : "text-gray-400 group-hover:text-white"
                            }`}
                          >
                            Default: {systemTimezone}
                          </span>
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                          <span
                            className={`text-[11px] font-mono px-2 py-0.5 rounded ${
                              timezone === "SYSTEM" || timezone === "System Time"
                                ? "bg-white/20 text-white"
                                : "bg-blue-50 text-[#088ADA]"
                            }`}
                          >
                            {systemTimezoneOffset}
                          </span>
                          {(timezone === "SYSTEM" || timezone === "System Time") && (
                            <Check className="h-4 w-4 text-white shrink-0 ml-1" />
                          )}
                        </div>
                      </button>

                      {TIMEZONE_OPTIONS.map((tz) => {
                        const isSelected = tz.value === timezone;
                        return (
                          <button
                            key={tz.value}
                            type="button"
                            onClick={() => handleSelectTimezone(tz.value)}
                            className={`w-full text-left px-3.5 py-2.5 text-sm flex items-center justify-between transition-colors duration-150 cursor-pointer ${
                              isSelected
                                ? "bg-[#088ADA] text-white font-medium"
                                : "text-gray-700 bg-white hover:bg-[#088ADA] hover:text-white"
                            }`}
                          >
                            <span className="truncate">
                              {tz.label} &nbsp; ({tz.offset})
                            </span>
                            {isSelected && (
                              <Check className="h-4 w-4 text-white shrink-0 ml-2" />
                            )}
                          </button>
                        );
                      })}
                      {timezone !== "SYSTEM" &&
                        timezone !== "System Time" &&
                        !TIMEZONE_OPTIONS.some((tz) => tz.value === timezone) && (
                          <button
                            type="button"
                            onClick={() => handleSelectTimezone(timezone)}
                            className="w-full text-left px-3.5 py-2.5 text-sm flex items-center justify-between bg-[#088ADA] text-white font-medium cursor-pointer"
                          >
                            <span className="truncate">{timezone} (Custom / Device)</span>
                            <Check className="h-4 w-4 text-white shrink-0 ml-2" />
                          </button>
                        )}
                    </div>
                  )}
                </div>
              </div>

              {/* Live Preview & Scope Explainer */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
                <div className="p-3 rounded-xl bg-gray-50 border border-gray-200/70 flex items-center justify-between">
                  <div className="space-y-0.5">
                    <span className="text-[11px] font-medium text-gray-500 flex items-center gap-1">
                      <Clock className="h-3 w-3 text-emerald-600" />
                      Live Preview ({timezone === "SYSTEM" || timezone === "System Time" ? `System Time: ${systemTimezone}` : timezone})
                    </span>
                    <p className="text-xs font-semibold text-gray-800 font-mono">
                      {formattedTzPreview}
                    </p>
                  </div>
                  <span className="h-2 w-2 rounded-full bg-emerald-500 animate-pulse" />
                </div>

                <div className="p-3 rounded-xl bg-blue-50/60 border border-blue-100 text-[11px] text-blue-800 flex items-center">
                  <span>
                    <strong>Only for you:</strong> this changes how times look for your account. Other users are not affected.
                  </span>
                </div>
              </div>
            </div>
          </div>

          {/* Action Bar */}
          <div className="flex items-center justify-end gap-3 pt-2">
            <button
              type="submit"
              disabled={saving}
              className="inline-flex items-center gap-2 px-6 py-2.5 rounded-xl bg-[#088ADA] hover:bg-[#0779bf] text-white text-sm font-semibold shadow-md transition disabled:opacity-50 cursor-pointer"
            >
              {saving ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span>Saving...</span>
                </>
              ) : (
                <>
                  <Save className="h-4 w-4" />
                  <span>Save changes</span>
                </>
              )}
            </button>
          </div>
        </form>
      )}

      <MfaStatusCard />
    </div>
  );
}
