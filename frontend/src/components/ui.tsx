"use client";

import React from "react";
import { AlertCircle, CheckCircle2, Info, AlertTriangle, X, Loader2 } from "lucide-react";

/* Shared, consistent building blocks for every dashboard page. */

export const btn = {
  primary:
    "inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-xl bg-[#088ADA] hover:bg-[#0778bd] text-white text-xs font-semibold shadow-sm transition disabled:opacity-50 disabled:cursor-not-allowed",
  secondary:
    "inline-flex items-center justify-center gap-1.5 px-3.5 py-2 rounded-xl bg-white hover:bg-gray-50 text-gray-700 text-xs font-medium border border-gray-200 shadow-sm transition disabled:opacity-50 disabled:cursor-not-allowed",
  danger:
    "inline-flex items-center justify-center gap-1.5 px-3.5 py-2 rounded-xl bg-rose-600 hover:bg-rose-700 text-white text-xs font-semibold shadow-sm transition disabled:opacity-50 disabled:cursor-not-allowed",
  dangerSoft:
    "inline-flex items-center justify-center gap-1.5 px-3.5 py-2 rounded-xl bg-rose-50 hover:bg-rose-100 text-rose-700 text-xs font-semibold border border-rose-200 transition disabled:opacity-50 disabled:cursor-not-allowed",
  success:
    "inline-flex items-center justify-center gap-1.5 px-4 py-2 rounded-xl bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-semibold shadow-sm transition disabled:opacity-50 disabled:cursor-not-allowed",
};

export const inputClass =
  "w-full px-3.5 py-2 bg-white border border-gray-300 rounded-xl text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:ring-2 focus:ring-[#088ADA]/20 transition";

export function PageHeader({
  icon: Icon,
  title,
  description,
  actions,
  badge,
}: {
  icon?: React.ComponentType<{ className?: string }>;
  title: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  badge?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-5 border-b border-gray-200">
      <div className="flex items-start gap-3 min-w-0">
        {Icon && (
          <div className="h-10 w-10 rounded-xl bg-[#088ADA]/10 text-[#088ADA] flex items-center justify-center shrink-0">
            <Icon className="h-5 w-5" />
          </div>
        )}
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <h1 className="text-xl font-semibold text-gray-900 tracking-tight">{title}</h1>
            {badge}
          </div>
          {description && <p className="text-sm text-gray-500 mt-0.5">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex items-center gap-2 flex-wrap shrink-0">{actions}</div>}
    </div>
  );
}

const ALERT_STYLES = {
  success: { box: "bg-emerald-50 border-emerald-200 text-emerald-800", icon: CheckCircle2, iconColor: "text-emerald-600" },
  error: { box: "bg-rose-50 border-rose-200 text-rose-800", icon: AlertCircle, iconColor: "text-rose-600" },
  warning: { box: "bg-amber-50 border-amber-200 text-amber-900", icon: AlertTriangle, iconColor: "text-amber-600" },
  info: { box: "bg-sky-50 border-sky-200 text-sky-900", icon: Info, iconColor: "text-sky-600" },
};

export function Alert({
  type = "info",
  title,
  children,
  onClose,
}: {
  type?: keyof typeof ALERT_STYLES;
  title?: React.ReactNode;
  children?: React.ReactNode;
  onClose?: () => void;
}) {
  const s = ALERT_STYLES[type];
  const Icon = s.icon;
  return (
    <div role={type === "error" ? "alert" : "status"} className={`flex items-start gap-3 p-3.5 rounded-xl border text-sm ${s.box}`}>
      <Icon className={`h-5 w-5 shrink-0 ${s.iconColor}`} />
      <div className="flex-1 min-w-0">
        {title && <p className="font-semibold">{title}</p>}
        {children && <div className={title ? "mt-0.5 text-[13px] opacity-90" : ""}>{children}</div>}
      </div>
      {onClose && (
        <button type="button" onClick={onClose} aria-label="Dismiss" className="p-1 rounded-lg hover:bg-black/5 opacity-60 hover:opacity-100 transition">
          <X className="h-4 w-4" />
        </button>
      )}
    </div>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
}: {
  icon?: React.ComponentType<{ className?: string }>;
  title: React.ReactNode;
  description?: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center text-center py-12 px-6 rounded-2xl border border-dashed border-gray-300 bg-white">
      {Icon && (
        <div className="h-12 w-12 rounded-2xl bg-gray-100 text-gray-400 flex items-center justify-center mb-3">
          <Icon className="h-6 w-6" />
        </div>
      )}
      <h3 className="text-sm font-semibold text-gray-800">{title}</h3>
      {description && <p className="text-sm text-gray-500 mt-1 max-w-md">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export function LoadingState({ label = "Loading..." }: { label?: string }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-gray-500">
      <Loader2 className="h-7 w-7 animate-spin text-[#088ADA]" />
      <p className="text-sm">{label}</p>
    </div>
  );
}

export function Card({ className = "", children }: { className?: string; children: React.ReactNode }) {
  return <div className={`bg-white border border-gray-200 rounded-2xl shadow-sm ${className}`}>{children}</div>;
}

export function SectionTitle({ title, description, right }: { title: React.ReactNode; description?: React.ReactNode; right?: React.ReactNode }) {
  return (
    <div className="flex items-end justify-between gap-3 mb-3">
      <div>
        <h2 className="text-sm font-semibold text-gray-800">{title}</h2>
        {description && <p className="text-xs text-gray-500 mt-0.5">{description}</p>}
      </div>
      {right}
    </div>
  );
}

export function StatCard({
  label,
  value,
  hint,
  icon: Icon,
  tone = "blue",
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  icon?: React.ComponentType<{ className?: string }>;
  tone?: "blue" | "green" | "amber" | "rose" | "gray";
}) {
  const tones = {
    blue: "bg-sky-50 text-[#088ADA]",
    green: "bg-emerald-50 text-emerald-600",
    amber: "bg-amber-50 text-amber-600",
    rose: "bg-rose-50 text-rose-600",
    gray: "bg-gray-100 text-gray-500",
  };
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-gray-500">{label}</span>
        {Icon && (
          <div className={`p-2 rounded-lg ${tones[tone]}`}>
            <Icon className="h-4 w-4" />
          </div>
        )}
      </div>
      <div className="mt-2 text-2xl font-semibold text-gray-900 tabular-nums">{value}</div>
      {hint && <p className="text-xs text-gray-500 mt-1">{hint}</p>}
    </Card>
  );
}

export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    pending: { label: "Waiting for review", cls: "bg-amber-50 text-amber-700 border-amber-200" },
    applying: { label: "Applying...", cls: "bg-sky-50 text-sky-700 border-sky-200" },
    applied: { label: "Approved & applied", cls: "bg-emerald-50 text-emerald-700 border-emerald-200" },
    rejected: { label: "Rejected", cls: "bg-rose-50 text-rose-700 border-rose-200" },
    expired: { label: "Expired", cls: "bg-gray-100 text-gray-600 border-gray-200" },
    failed: { label: "Failed", cls: "bg-rose-50 text-rose-700 border-rose-200" },
  };
  const s = map[status] || { label: status, cls: "bg-gray-100 text-gray-600 border-gray-200" };
  return <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold border ${s.cls}`}>{s.label}</span>;
}

export function ComingSoonBadge() {
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold bg-gray-100 text-gray-500 border border-gray-200">
      Coming soon
    </span>
  );
}

export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel = "Confirm",
  confirmClass = btn.primary,
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: React.ReactNode;
  children?: React.ReactNode;
  confirmLabel?: React.ReactNode;
  confirmClass?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  React.useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && !busy) onCancel();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, busy, onCancel]);

  if (!open) return null;
  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 backdrop-blur-sm flex items-center justify-center p-4"
      onClick={() => !busy && onCancel()}
    >
      <div
        role="dialog"
        aria-modal="true"
        className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-base font-semibold text-gray-900">{title}</h3>
        {children && <div className="text-sm text-gray-600 space-y-2">{children}</div>}
        <div className="flex items-center justify-end gap-2 pt-2">
          <button type="button" onClick={onCancel} disabled={busy} className={btn.secondary}>
            Cancel
          </button>
          <button type="button" onClick={onConfirm} disabled={busy} className={confirmClass} autoFocus>
            {busy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
