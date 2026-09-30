export interface SystemStats {
  jobs: {
    total: number;
    pending: number;
    processing: number;
    completed: number;
    failed: number;
  };
  approvals: {
    total: number;
    pending: number;
    applied: number;
    rejected: number;
    expired: number;
  };
  conversations: number;
  snapshots: number;
}

export interface Approval {
  id: number;
  approval_id: string;
  team_id: string;
  channel_id: string;
  channel_name?: string;
  thread_ts: string;
  message_ts: string;
  user_id: string;
  user_name?: string;
  tool_name: string;
  tool_arguments: Record<string, any>;
  status: "pending" | "applying" | "applied" | "rejected" | "expired" | "failed";
  approved_by?: string;
  approved_by_name?: string;
  execution_result?: string;
  expires_at?: string;
  created_at: string;
  updated_at: string;
  diff_preview?: string;
  /** 'diff' = real comparison with GitHub, 'full' = whole new file (comparison unavailable), 'text' = not a file change */
  diff_kind?: "diff" | "full" | "text";
}

export interface LogEvent {
  id?: string;
  timestamp: string;
  level: "INFO" | "WARN" | "ERROR" | "DEBUG";
  action: string;
  category: "WORKER" | "CLAUDE" | "TOOL" | "SLACK" | "DATABASE" | "FILTER" | "CONTEXT" | string;
  thread_id?: string;
  event_id?: string;
  user_id?: string;
  channel_id?: string;
  channel_name?: string;
  session_id?: string;
  message: string;
  extra?: Record<string, any>;
}

export interface ContextSnapshot {
  id: number;
  channel_id: string;
  thread_ts: string;
  user_id: string;
  user_name: string;
  session_id: string;
  model: string;
  prompt_text: string;
  system_prompt?: string;
  messages_sent: Array<{
    role: string;
    speaker: string;
    content: string;
  }>;
  files_included?: string[];
  created_at: string;
}

export interface TableInfo {
  name: string;
  row_count: number;
}

export interface TableColumn {
  name: string;
  type: string;
}

export interface ChannelFolder {
  id: number;
  name: string;
  description?: string;
  channel_count: number;
  created_at?: string;
  updated_at?: string;
}

export interface FolderDetails extends ChannelFolder {
  channels: ChannelProject[];
}

export interface CreateSlackChannelPayload {
  name: string;
  is_private?: boolean;
  topic?: string;
}


export interface ChannelProject {
  workspace_id?: string;
  workspace_name?: string;
  channel_id: string;
  channel_name: string;
  channel_type?: string;
  secret_count: number;
  providers: string[];
  folder_id?: number | null;
  folder_name?: string | null;
}

export interface ChannelSecretMetadata {
  id: number;
  channel_id: string;
  channel_name?: string;
  provider: string;
  aws_secret_name: string;
  aws_secret_arn?: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  updated_by?: string;
}

export interface SaveSecretPayload {
  provider: string;
  api_key: string;
  channel_name?: string;
}

export interface AuthUser {
  username: string;
  role: string;
}

export interface LoginResponse {
  status: string;
  message: string;
  token: string;
  user: AuthUser;
}

export interface VaultSecret {
  id: number;
  key_name: string;
  aws_secret_name: string;
  name?: string;
  full_name?: string;
  created_at: string;
  updated_at?: string;
}

export interface ChannelUsage {
  workspace_id?: string;
  workspace_name?: string;
  channel_id: string;
  channel_name: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
}

export interface UserUsage {
  workspace_id?: string;
  workspace_name?: string;
  user_id: string;
  user_name?: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
}

export interface ChannelUserUsage {
  workspace_id?: string;
  workspace_name?: string;
  channel_id: string;
  channel_name: string;
  user_id: string;
  user_name?: string;
  calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
}

export interface UsageSummary {
  total_calls: number;
  total_input_tokens: number;
  total_output_tokens: number;
  total_tokens: number;
  total_cost_usd: number;
  active_channels_count?: number;
  active_users_count?: number;
  workspaces?: Array<{ workspace_id: string; workspace_name: string }>;
  by_channel: ChannelUsage[];
  by_user?: UserUsage[];
  by_channel_user?: ChannelUserUsage[];
}

export interface ApiUsageLog {
  id: number;
  workspace_id?: string;
  workspace_name?: string;
  channel_id: string;
  channel_name: string;
  user_id?: string;
  user_name?: string;
  model: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  created_at: string;
}

export interface DashboardUser {
  id: number;
  name?: string;
  username: string;
  email?: string;
  role: "jts_admin" | "client_admin" | "client_standard";
  timezone?: string;
  client_folder_id?: number | null;
  client_folder_name?: string | null;
  organization_id?: number | null;
  organization_name?: string | null;
  created_at?: string;
}

export interface VerifyTokenResponse {
  valid: boolean;
  username?: string;
  name?: string;
  email?: string;
  message?: string;
}


export interface ConversationMessage {
  id: number;
  team_id?: string;
  workspace_name?: string;
  channel_id: string;
  thread_ts?: string;
  user_id: string;
  user_name?: string;
  role: string;
  content: string;
  message_ts?: string;
  created_at: string;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  cost_usd?: number;
}

export function getUserProfileTimezone(): string {
  if (typeof window === "undefined") return "UTC";
  try {
    const globalTz = localStorage.getItem("jts_global_timezone") || sessionStorage.getItem("jts_global_timezone") || "UTC";

    const resolveTz = (tz?: string | null): string | null => {
      if (!tz || !tz.trim()) return null;
      const clean = tz.trim();
      if (clean.toUpperCase() === "SYSTEM" || clean.toLowerCase() === "system time") {
        return globalTz;
      }
      return clean;
    };

    const tzDirect = resolveTz(sessionStorage.getItem("jts_user_timezone"));
    if (tzDirect) return tzDirect;

    const localTz = resolveTz(localStorage.getItem("jts_user_timezone"));
    if (localTz) return localTz;

    const rawUser = sessionStorage.getItem("jts_user");
    if (rawUser) {
      const u = JSON.parse(rawUser);
      const userTz = resolveTz(u?.timezone);
      if (userTz) return userTz;
    }

    const rawLocalUser = localStorage.getItem("jts_user");
    if (rawLocalUser) {
      const u = JSON.parse(rawLocalUser);
      const userTz = resolveTz(u?.timezone);
      if (userTz) return userTz;
    }

    if (typeof document !== "undefined") {
      const match = document.cookie.match(/(?:^|;\s*)jts_user_tz=([^;]+)/);
      if (match && match[1]) {
        const cookieTz = resolveTz(decodeURIComponent(match[1]));
        if (cookieTz) return cookieTz;
      }
    }

    if (globalTz && globalTz.trim()) return globalTz.trim();
  } catch {}
  return "UTC";
}

export function getGlobalDateFormat(): string {
  if (typeof window === "undefined") return "YYYY-MM-DD";
  try {
    const df = localStorage.getItem("jts_date_format") || sessionStorage.getItem("jts_date_format");
    if (df && df.trim()) return df.trim();
  } catch {}
  return "YYYY-MM-DD";
}

export function getGlobalTimeFormat(): "12h" | "24h" {
  if (typeof window === "undefined") return "12h";
  try {
    const tf = localStorage.getItem("jts_time_format") || sessionStorage.getItem("jts_time_format");
    if (tf === "24h" || tf === "12h") return tf;
  } catch {}
  return "12h";
}

export function getGlobalShowSeconds(): boolean {
  if (typeof window === "undefined") return true;
  try {
    const ss = localStorage.getItem("jts_show_seconds") ?? sessionStorage.getItem("jts_show_seconds");
    if (ss !== null && ss !== undefined) {
      return ss === "true" || ss === "1";
    }
  } catch {}
  return true;
}

export function formatLocalDateTime(
  dateStr: string | number | Date | null | undefined,
  options?: {
    dateFormat?: string;
    timeFormat?: "12h" | "24h" | string;
    showSeconds?: boolean;
    timezone?: string;
  }
): string {
  if (!dateStr) return "-";
  try {
    let d: Date;
    if (dateStr instanceof Date) {
      d = dateStr;
    } else if (typeof dateStr === "number") {
      d = dateStr < 1e11 ? new Date(dateStr * 1000) : new Date(dateStr);
    } else {
      let str = String(dateStr).trim();
      if (!str) return "-";
      if (/^\d{9,11}(\.\d+)?$/.test(str)) {
        d = new Date(parseFloat(str) * 1000);
      } else if (/^\d{12,14}$/.test(str)) {
        d = new Date(parseInt(str, 10));
      } else {
        if (/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}/.test(str)) {
          str = str.replace(" ", "T");
          if (!str.endsWith("Z") && !/[+-]\d{2}(:?\d{2})?$/.test(str)) {
            str = str + "Z";
          }
        }
        d = new Date(str);
      }
    }

    if (isNaN(d.getTime())) return String(dateStr);

    const targetTz = options?.timezone || getUserProfileTimezone();
    const targetDateFormat = options?.dateFormat || getGlobalDateFormat();
    const targetTimeFormat = (options?.timeFormat as "12h" | "24h") || getGlobalTimeFormat();
    const targetShowSeconds = options?.showSeconds !== undefined ? options.showSeconds : getGlobalShowSeconds();

    const is12Hour = targetTimeFormat === "12h";
    const isMonthName = targetDateFormat.includes("MMM");

    const formatterOptions: Intl.DateTimeFormatOptions = {
      timeZone: targetTz,
      year: "numeric",
      month: isMonthName ? "short" : "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: is12Hour,
    };

    let parts: Intl.DateTimeFormatPart[];
    try {
      const formatter = new Intl.DateTimeFormat("en-US", formatterOptions);
      parts = formatter.formatToParts(d);
    } catch {
      // Fallback if specified timezone string is unparseable
      const fallbackFormatter = new Intl.DateTimeFormat("en-US", {
        ...formatterOptions,
        timeZone: "UTC",
      });
      parts = fallbackFormatter.formatToParts(d);
    }

    const map: Record<string, string> = {};
    for (const p of parts) {
      if (p.type === "dayPeriod") {
        map[p.type] = p.value.toUpperCase();
      } else {
        map[p.type] = p.value;
      }
    }

    const year = map.year || "1970";
    const month = map.month || "01";
    const day = map.day || "01";
    const hour = map.hour || (is12Hour ? "12" : "00");
    const minute = map.minute || "00";
    const second = map.second || "00";
    const dayPeriod = map.dayPeriod ? ` ${map.dayPeriod}` : (is12Hour ? " AM" : "");

    // Build Date portion based on chosen date_format
    let formattedDate = `${year}-${month}-${day}`;
    if (targetDateFormat === "DD/MM/YYYY") {
      formattedDate = `${day}/${month}/${year}`;
    } else if (targetDateFormat === "MM/DD/YYYY") {
      formattedDate = `${month}/${day}/${year}`;
    } else if (targetDateFormat === "DD MMM YYYY") {
      formattedDate = `${day} ${month} ${year}`;
    } else {
      // default "YYYY-MM-DD"
      formattedDate = `${year}-${month}-${day}`;
    }

    // Build Time portion based on chosen time_format & show_seconds
    let formattedTime = "";
    if (is12Hour) {
      if (targetShowSeconds) {
        formattedTime = `${hour}:${minute}:${second}${dayPeriod}`;
      } else {
        formattedTime = `${hour}:${minute}${dayPeriod}`;
      }
    } else {
      if (targetShowSeconds) {
        formattedTime = `${hour}:${minute}:${second}`;
      } else {
        formattedTime = `${hour}:${minute}`;
      }
    }

    return `${formattedDate} ${formattedTime}`.trim();
  } catch {
    return String(dateStr);
  }
}

export interface Organization {
  id: number;
  name: string;
  poc: string;
  phone: string;
  email: string;
  billing_email: string;
  address: string;
  workspace_id?: string;
  workspace_name?: string;
  created_at?: string;
  updated_at?: string;
}

export interface CreateOrganizationPayload {
  name: string;
  poc?: string;
  phone?: string;
  email?: string;
  billing_email?: string;
  address?: string;
}

export interface GlobalSettingsData {
  timezone: string;
  date_format: string;
  time_format: "12h" | "24h" | string;
  show_seconds: boolean;
  auto_dst: boolean;
  sync_alerts: boolean;
  updated_at?: string;
  updated_by?: string;
}

export interface UpdateGlobalSettingsPayload {
  timezone?: string;
  date_format?: string;
  time_format?: "12h" | "24h" | string;
  show_seconds?: boolean;
  auto_dst?: boolean;
  sync_alerts?: boolean;
}

// A bot reply with real tokens but zero cost was made with the client's own API key: not billed.
export function isClientKeyMessage(m: { cost_usd?: number; total_tokens?: number }): boolean {
  return Number(m.cost_usd || 0) === 0 && Number(m.total_tokens || 0) > 0;
}

export interface FolderApiKeyStatus {
  folder_id: number;
  provider: string;
  configured: boolean;
  billing_mode: "client_key" | "jts_billed";
  key_hint: string | null;
  updated_by: string | null;
  updated_at: string | null;
  key_status?: "none" | "ok" | "failing";
  last_error?: string | null;
  last_error_at?: string | null;
}




export interface InvoiceLineItem {
  channel_id: string;
  channel_name: string;
  replies: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
}

export interface InvoiceBillTo {
  organization_id?: number;
  name?: string;
  contact?: string | null;
  email?: string | null;
  phone?: string | null;
  address?: string | null;
}

export interface Invoice {
  id: number;
  invoice_number: string;
  folder_id: number;
  folder_name: string;
  organization_id?: number | null;
  bill_to: InvoiceBillTo;
  period_start: string;
  period_end: string;
  replies: number;
  total_tokens: number;
  usage_cost_usd: number;
  markup_percent: number;
  amount_usd: number;
  line_items: InvoiceLineItem[];
  status: "unpaid" | "paid" | "void";
  /** status, or "overdue" when unpaid past the due date */
  display_status: "unpaid" | "paid" | "void" | "overdue";
  due_date?: string | null;
  notes?: string | null;
  paid_at?: string | null;
  payment_reference?: string | null;
  void_reason?: string | null;
  created_by?: string | null;
  created_at?: string;
}

export interface InvoicePreview {
  folder_id: number;
  folder_name: string;
  channel_count: number;
  line_items: InvoiceLineItem[];
  replies: number;
  total_tokens: number;
  usage_cost_usd: number;
  markup_percent: number;
  amount_usd: number;
  suggested_organization_id: number | null;
  overlapping_invoice: string | null;
}
