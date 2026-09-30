import {
  SystemStats,
  Approval,
  ContextSnapshot,
  TableInfo,
  TableColumn,
  ChannelProject,
  ChannelSecretMetadata,
  ChannelFolder,
  FolderDetails,
  CreateSlackChannelPayload,
  SaveSecretPayload,
  LoginResponse,
  AuthUser,
  VaultSecret,
  UsageSummary,
  ApiUsageLog,
  Invoice,
  InvoicePreview,
  DashboardUser,
  ConversationMessage,
  VerifyTokenResponse,
  GlobalSettingsData,
  UpdateGlobalSettingsPayload,
  FolderApiKeyStatus,
} from "./types";

const API_BASE = ""; // Relative path works automatically via Next.js rewrites

export function extractErrorMessage(errData: any, fallback: string = "An error occurred"): string {
  if (!errData) return fallback;
  if (typeof errData === "string") return errData;
  if (typeof errData.detail === "string") return errData.detail;
  if (!errData.detail && typeof errData.message === "string") return errData.message;
  if (Array.isArray(errData.detail)) {
    return errData.detail
      .map((d: any) => {
        if (typeof d === "string") return d;
        const field = Array.isArray(d.loc) ? d.loc.slice(-1)[0] : "";
        const msg = d.msg || d.message || JSON.stringify(d);
        return field && field !== "body" ? `${field}: ${msg}` : msg;
      })
      .join("; ");
  }
  if (errData.detail && typeof errData.detail === "object") {
    return errData.detail.message || errData.detail.msg || JSON.stringify(errData.detail);
  }
  if (errData.message && typeof errData.message === "string") return errData.message;
  if (errData.error && typeof errData.error === "string") return errData.error;
  return fallback;
}

function getAuthHeaders(extraHeaders: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { ...extraHeaders };
  if (typeof window !== "undefined") {
    const token = sessionStorage.getItem("jts_token");
    if (token) {
      headers["Authorization"] = `Bearer ${token}`;
    }
    const simRole = sessionStorage.getItem("jts_simulated_role");
    if (simRole) {
      headers["X-JTS-Simulated-Role"] = simRole;
    }
    const rawUser = sessionStorage.getItem("jts_user");
    if (rawUser) {
      try {
        const u = JSON.parse(rawUser);
        if (u.role) {
          headers["X-JTS-Role"] = u.role;
        }
      } catch {}
    }
  }
  return headers;
}

export async function fetchStats(): Promise<SystemStats> {
  const res = await fetch(`${API_BASE}/api/stats`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Failed to fetch system stats");
  return res.json();
}

export async function fetchApprovals(status?: string): Promise<Approval[]> {
  const url = status ? `${API_BASE}/api/approvals?status=${encodeURIComponent(status)}` : `${API_BASE}/api/approvals`;
  const res = await fetch(url, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Failed to fetch approvals");
  const data = await res.json();
  return data.approvals || [];
}

export async function fetchApprovalsWithAccess(
  status?: string
): Promise<{ approvals: Approval[]; canApprove: boolean }> {
  const url = status ? `${API_BASE}/api/approvals?status=${encodeURIComponent(status)}` : `${API_BASE}/api/approvals`;
  const res = await fetch(url, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) throw new Error("We couldn't load approvals. Please refresh the page.");
  const data = await res.json();
  return { approvals: data.approvals || [], canApprove: Boolean(data.can_approve) };
}

export async function fetchApproval(approvalId: string): Promise<Approval> {
  const res = await fetch(`${API_BASE}/api/approvals/${encodeURIComponent(approvalId)}`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Failed to fetch approval ${approvalId}`);
  const data = await res.json();
  return data.approval;
}

export async function submitApprovalAction(
  approvalId: string,
  action: "approve" | "reject",
  userId: string = "Admin User"
): Promise<{ ok: boolean; status: string; execution_result?: string; message?: string }> {
  const res = await fetch(`${API_BASE}/api/approvals/${encodeURIComponent(approvalId)}/action`, {
    method: "POST",
    headers: getAuthHeaders({ "Content-Type": "application/json" }),
    credentials: "include",
    body: JSON.stringify({ action, user_id: userId }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(extractErrorMessage(data, `Failed to ${action} approval`));
  }
  return data;
}

export const AUTHORITATIVE_CHANNEL_WORKSPACES: Record<string, { id: string; name: string }> = {
  "C08MV3EM9PY": { id: "T5ZMF56H5", name: "Axcel World" },
  "C0BMV3EM9PY": { id: "T5ZMF56H5", name: "Axcel World" },
  "C08V6S5UJ0P": { id: "T5ZMF56H5", name: "Axcel World" },
  "C0BV6S5UJ0P": { id: "T5ZMF56H5", name: "Axcel World" },
  "D08SLP9LXUZ": { id: "T5ZMF56H5", name: "Axcel World" },
  "D0BSLP9LXUZ": { id: "T5ZMF56H5", name: "Axcel World" },
  "C0C28B8V2PK": { id: "T02HKMBE09K", name: "JTS Team" },
};

export const AUTHORITATIVE_CHANNEL_NAMES: Record<string, string> = {
  "C08MV3EM9PY": "#jts_powertool",
  "C0BMV3EM9PY": "#jts_powertool",
  "C08V6S5UJ0P": "#agents_working_projects",
  "C0BV6S5UJ0P": "#agents_working_projects",
  "D08SLP9LXUZ": "@Admin User (DM)",
  "D0BSLP9LXUZ": "@Admin User (DM)",
  "C0C28B8V2PK": "#jts_powertool",
};

export function getAuthoritativeWorkspace(
  channelId?: string,
  currentWsId?: string,
  currentWsName?: string
): { id: string; name: string } {
  if (channelId) {
    const clean = channelId.trim().toUpperCase();
    if (AUTHORITATIVE_CHANNEL_WORKSPACES[clean]) {
      return AUTHORITATIVE_CHANNEL_WORKSPACES[clean];
    }
  }
  return {
    id: currentWsId || "T5ZMF56H5",
    name: currentWsName || "Axcel World",
  };
}

export function canonicalChannelId(cid: string, ws?: string, channelName?: string): string {
  if (!cid) return cid;
  const clean = cid.trim().toUpperCase();
  const wsLower = (ws || "").toLowerCase();
  const nameLower = (channelName || "").toLowerCase();

  // JTS Team workspace channel #jts_powertool MUST be C0C28B8V2PK
  if (wsLower.includes("jts") || ws === "T02HKMBE09K") {
    if (clean === "C08MV3EM9PY" || clean === "C0BMV3EM9PY" || clean === "C0C28B8V2PK" || nameLower === "#jts_powertool" || nameLower === "jts_powertool") {
      return "C0C28B8V2PK";
    }
  }

  // Axcel World workspace channel #jts_powertool MUST be C08MV3EM9PY
  if (wsLower.includes("axcel") || ws === "T5ZMF56H5") {
    if (clean === "C0BMV3EM9PY" || clean === "C08MV3EM9PY" || nameLower === "#jts_powertool" || nameLower === "jts_powertool") {
      return "C08MV3EM9PY";
    }
  }

  if (clean === "C0BMV3EM9PY") return "C08MV3EM9PY";
  if (clean === "C0BV6S5UJ0P") return "C08V6S5UJ0P";
  if (clean === "D0BSLP9LXUZ") return "D08SLP9LXUZ";
  return cid.trim();
}

export async function fetchLatestContext(): Promise<ContextSnapshot | null> {
  const res = await fetch(`${API_BASE}/context/api/latest`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) return null;
  const data = await res.json();
  return data.snapshot;
}

export async function fetchContextHistory(limit: number = 25): Promise<ContextSnapshot[]> {
  const res = await fetch(`${API_BASE}/context/api/history?limit=${limit}`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.history || [];
}

export async function fetchContextSnapshotById(snapshotId: number): Promise<ContextSnapshot | null> {
  const res = await fetch(`${API_BASE}/context/api/snapshot/${snapshotId}`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) return null;
  const data = await res.json();
  return data.snapshot;
}

export async function fetchTables(): Promise<TableInfo[]> {
  const res = await fetch(`${API_BASE}/database/api/tables`, {
    headers: getAuthHeaders(),
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) return [];
  const data = await res.json();
  return data.tables || [];
}

export async function fetchTableData(tableName: string): Promise<{ columns: TableColumn[]; rows: any[]; total_rows?: number; total?: number }> {
  const res = await fetch(`${API_BASE}/database/api/table/${encodeURIComponent(tableName)}`, {
    headers: getAuthHeaders(),
    credentials: "same-origin",
    cache: "no-store",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: `Failed to load table ${tableName}` }));
    throw new Error(err.detail || `Failed to load table ${tableName}`);
  }
  return res.json();
}

export async function clearTableData(tableName: string): Promise<{ status: string; message: string }> {
  const res = await fetch(`${API_BASE}/database/api/table/${encodeURIComponent(tableName)}/clear`, {
    method: "POST",
    headers: getAuthHeaders(),
    credentials: "same-origin",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to clear table data" }));
    throw new Error(err.detail || "Failed to clear table data");
  }
  return res.json();
}

export async function clearFullDatabase(): Promise<{ status: string; message: string; cleared: string[]; errors?: string[] }> {
  const res = await fetch(`${API_BASE}/database/api/clear-all`, {
    method: "POST",
    headers: getAuthHeaders(),
    credentials: "same-origin",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to clear full database" }));
    throw new Error(err.detail || "Failed to clear full database");
  }
  return res.json();
}

export async function fetchChannels(): Promise<ChannelProject[]> {
  const res = await fetch(`${API_BASE}/api/channels`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Failed to fetch channels");
  const data = await res.json();
  return data.channels || [];
}

export async function fetchChannelSecrets(channelId: string): Promise<ChannelSecretMetadata[]> {
  const res = await fetch(`${API_BASE}/api/channels/${encodeURIComponent(channelId)}/secrets`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Failed to fetch secrets for channel ${channelId}`);
  const data = await res.json();
  return data.secrets || [];
}

export async function saveChannelSecret(
  channelId: string,
  payload: SaveSecretPayload,
  userId: string = "web-admin"
): Promise<{ status: string; message: string; mapping: ChannelSecretMetadata }> {
  const res = await fetch(`${API_BASE}/api/channels/${encodeURIComponent(channelId)}/secrets`, {
    method: "POST",
    headers: getAuthHeaders({
      "Content-Type": "application/json",
      "X-JTS-User-Id": userId,
    }),
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to store secret" }));
    throw new Error(err.detail || "Failed to store secret in AWS Secrets Manager");
  }
  return res.json();
}

export async function deleteChannelSecret(
  channelId: string,
  provider: string,
  userId: string = "web-admin"
): Promise<{ status: string; message: string }> {
  const res = await fetch(`${API_BASE}/api/channels/${encodeURIComponent(channelId)}/secrets/${encodeURIComponent(provider)}`, {
    method: "DELETE",
    headers: getAuthHeaders({
      "X-JTS-User-Id": userId,
    }),
  });
  if (!res.ok) throw new Error(`Failed to delete secret for provider ${provider}`);
  return res.json();
}

export async function updateChannelName(
  channelId: string,
  channelName: string
): Promise<{ status: string; message: string; channel_id: string; channel_name: string }> {
  const res = await fetch(`${API_BASE}/api/channels/${encodeURIComponent(channelId)}/name`, {
    method: "PATCH",
    headers: getAuthHeaders({
      "Content-Type": "application/json",
    }),
    body: JSON.stringify({ channel_name: channelName }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to update channel name" }));
    throw new Error(err.detail || "Failed to update channel name");
  }
  return res.json();
}

export async function fetchFolders(): Promise<ChannelFolder[]> {
  const res = await fetch(`${API_BASE}/api/channels/folders`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Failed to fetch channel folders");
  const data = await res.json();
  return data.folders || [];
}

export async function createFolder(
  name: string,
  description?: string
): Promise<{ status: string; message: string; folder: ChannelFolder }> {
  const res = await fetch(`${API_BASE}/api/channels/folders`, {
    method: "POST",
    headers: getAuthHeaders({
      "Content-Type": "application/json",
    }),
    body: JSON.stringify({ name, description }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to create folder" }));
    throw new Error(err.detail || "Failed to create folder");
  }
  return res.json();
}

export async function updateFolder(
  folderId: number,
  name: string,
  description?: string
): Promise<{ status: string; message: string; folder: ChannelFolder }> {
  const res = await fetch(`${API_BASE}/api/channels/folders/${folderId}`, {
    method: "PATCH",
    headers: getAuthHeaders({
      "Content-Type": "application/json",
    }),
    body: JSON.stringify({ name, description }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to update folder" }));
    throw new Error(err.detail || "Failed to update folder");
  }
  return res.json();
}

export async function deleteFolder(folderId: number): Promise<{ status: string; message: string }> {
  const res = await fetch(`${API_BASE}/api/channels/folders/${folderId}`, {
    method: "DELETE",
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to delete folder" }));
    throw new Error(err.detail || "Failed to delete folder");
  }
  return res.json();
}

export async function fetchFolder(
  folderId: number | string
): Promise<{ status: string; folder: FolderDetails; channels: ChannelProject[] }> {
  try {
    const res = await fetch(`${API_BASE}/api/channels/folders/${folderId}`, {
      headers: getAuthHeaders(),
      cache: "no-store",
    });
    if (res.ok) {
      return await res.json();
    }
  } catch {
    // If network error on direct endpoint, attempt fallback
  }

  // Graceful fallback: assemble folder and channels from list endpoints
  try {
    const [allFolders, allChannels] = await Promise.all([
      fetchFolders(),
      fetchChannels().catch(() => []),
    ]);

    const numId = Number(folderId);
    const targetFolder = allFolders.find((f) => f.id === numId);
    if (targetFolder) {
      const folderChannels = allChannels.filter((c) => c.folder_id === numId);
      return {
        status: "success",
        folder: {
          ...targetFolder,
          channels: folderChannels,
          channel_count: folderChannels.length,
        },
        channels: folderChannels,
      };
    }
  } catch (fallbackErr) {
    console.error("Fallback folder fetch failed:", fallbackErr);
  }

  throw new Error(`Folder #${folderId} not found in database.`);
}

export async function createSlackChannel(
  folderId: number | string,
  payload: CreateSlackChannelPayload
): Promise<{ status: string; message: string; channel: ChannelProject }> {
  const res = await fetch(`${API_BASE}/api/channels/folders/${folderId}/channels`, {
    method: "POST",
    headers: getAuthHeaders({
      "Content-Type": "application/json",
    }),
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to create Slack channel" }));
    if (res.status === 404) {
      throw new Error(
        "Backend endpoint not found. Please restart the backend service on EC2: sudo systemctl restart jts-powertool"
      );
    }
    throw new Error(err.detail || "Failed to create Slack channel");
  }
  return res.json();
}

export async function syncSlackChannels(): Promise<{
  status: string;
  message: string;
  synced_count: number;
  channels: ChannelProject[];
}> {
  const res = await fetch(`${API_BASE}/api/channels/sync`, {
    method: "POST",
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to sync Slack channels" }));
    throw new Error(err.detail || "Failed to sync Slack channels");
  }
  return res.json();
}

export async function fetchUnassignedChannels(): Promise<ChannelProject[]> {
  const res = await fetch(`${API_BASE}/api/channels/unassigned`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to fetch unassigned channels" }));
    throw new Error(err.detail || "Failed to fetch unassigned channels");
  }
  const data = await res.json();
  return data.channels || [];
}



export async function assignChannelFolder(
  channelId: string,
  folderId: number | null
): Promise<{ status: string; message: string; folder_id: number | null; folder_name: string | null }> {
  const res = await fetch(`${API_BASE}/api/channels/${encodeURIComponent(channelId)}/folder`, {
    method: "PATCH",
    headers: getAuthHeaders({
      "Content-Type": "application/json",
    }),
    body: JSON.stringify({ folder_id: folderId }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to assign folder" }));
    throw new Error(err.detail || "Failed to assign folder");
  }
  return res.json();
}

export async function fetchFolderApiKey(folderId: number | string): Promise<FolderApiKeyStatus> {
  const res = await fetch(`${API_BASE}/api/channels/folders/${folderId}/anthropic-key`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(extractErrorMessage(err, "Failed to load API key status"));
  }
  return res.json();
}

export async function saveFolderApiKey(
  folderId: number | string,
  apiKey: string
): Promise<FolderApiKeyStatus & { status: string; message: string }> {
  const res = await fetch(`${API_BASE}/api/channels/folders/${folderId}/anthropic-key`, {
    method: "PUT",
    headers: getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify({ api_key: apiKey }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(extractErrorMessage(err, "Failed to save API key"));
  }
  return res.json();
}

export async function deleteFolderApiKey(
  folderId: number | string
): Promise<FolderApiKeyStatus & { status: string; message: string }> {
  const res = await fetch(`${API_BASE}/api/channels/folders/${folderId}/anthropic-key`, {
    method: "DELETE",
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(extractErrorMessage(err, "Failed to remove API key"));
  }
  return res.json();
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    if (res.status === 401) {
      throw new Error("Incorrect username or password. Please try again.");
    }
    throw new Error("We can't reach the server right now. Please try again in a moment.");
  }
  const data = await res.json();
  if (typeof window !== "undefined") {
    // Store credentials strictly in current tab's sessionStorage for 100% per-tab isolation
    sessionStorage.setItem("jts_user", JSON.stringify(data.user));
    sessionStorage.setItem("jts_token", data.token);
    if (data.user?.timezone) {
      sessionStorage.setItem("jts_user_timezone", data.user.timezone);
    }
    // Clear legacy cookie if present
    document.cookie = "jts_session=; Path=/; Expires=Thu, 01 Jan 1970 00:00:01 GMT;";
  }
  return data;
}

export async function logout(): Promise<void> {
  if (typeof window !== "undefined") {
    // Clear strictly current tab's session storage
    sessionStorage.clear();
    // Clear legacy cookie if present
    document.cookie = "jts_session=; Path=/; Expires=Thu, 01 Jan 1970 00:00:01 GMT;";
    window.location.href = "/login";
  }
}

export async function checkAuth(): Promise<{ authenticated: boolean; user?: AuthUser }> {
  try {
    const token = typeof window !== "undefined"
      ? sessionStorage.getItem("jts_token")
      : null;
    const res = await fetch(`${API_BASE}/api/auth/me`, {
      headers: {
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      cache: "no-store",
    });
    if (!res.ok) return { authenticated: false };
    return res.json();
  } catch {
    return { authenticated: false };
  }
}

export async function fetchMyProfile(): Promise<DashboardUser> {
  const res = await fetch(`${API_BASE}/api/users/me`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to fetch user profile" }));
    throw new Error(err.detail || "Failed to fetch user profile");
  }
  const data = await res.json();
  return data.user || data;
}

export async function updateMyProfile(payload: {
  name: string;
  email?: string;
  password?: string;
  timezone?: string;
}): Promise<{ status: string; message: string; user: DashboardUser }> {
  const res = await fetch(`${API_BASE}/api/users/me`, {
    method: "PUT",
    headers: getAuthHeaders({
      "Content-Type": "application/json",
    }),
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to update profile" }));
    throw new Error(err.detail || "Failed to update profile");
  }
  return res.json();
}

export async function fetchVaultSecrets(): Promise<VaultSecret[]> {
  const res = await fetch(`${API_BASE}/api/vault`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Failed to fetch secrets vault");
  const data = await res.json();
  return data.secrets || [];
}

export async function saveVaultSecret(
  keyName: string,
  keyValue: string
): Promise<{ status: string; message: string; secret: VaultSecret }> {
  const res = await fetch(`${API_BASE}/api/vault`, {
    method: "POST",
    headers: getAuthHeaders({
      "Content-Type": "application/json",
    }),
    body: JSON.stringify({ key_name: keyName, key_value: keyValue }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to store secret in vault" }));
    throw new Error(err.detail || "Failed to store secret in vault");
  }
  return res.json();
}

export async function deleteVaultSecret(
  vaultId: number
): Promise<{ status: string; message: string }> {
  const res = await fetch(`${API_BASE}/api/vault/${vaultId}`, {
    method: "DELETE",
    headers: getAuthHeaders(),
  });
  if (!res.ok) throw new Error("Failed to delete secret from vault");
  return res.json();
}

export async function fetchUsageSummary(): Promise<UsageSummary> {
  const res = await fetch(`${API_BASE}/api/usage/summary`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) {
    return {
      total_calls: 0,
      total_input_tokens: 0,
      total_output_tokens: 0,
      total_tokens: 0,
      total_cost_usd: 0,
      active_channels_count: 0,
      active_users_count: 0,
      by_channel: [],
      by_user: [],
      by_channel_user: [],
    };
  }
  return res.json();
}

/** Inclusive YYYY-MM-DD dates (UTC). Omit a side for an open-ended range. */
export type DateRange = { start?: string; end?: string };

function rangeQuery(range?: DateRange, extra: Record<string, string | number | undefined> = {}): string {
  const params = new URLSearchParams();
  if (range?.start) params.set("start", range.start);
  if (range?.end) params.set("end", range.end);
  Object.entries(extra).forEach(([k, v]) => {
    if (v !== undefined && v !== "") params.set(k, String(v));
  });
  const q = params.toString();
  return q ? `?${q}` : "";
}

export async function fetchBillingSummary(range?: DateRange): Promise<UsageSummary> {
  const res = await fetch(`${API_BASE}/api/usage/billing${rangeQuery(range)}`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractErrorMessage(data, "We couldn't load billing data. Please refresh the page."));
  return data;
}

export async function fetchUsageLogsInRange(limit: number, range?: DateRange): Promise<ApiUsageLog[]> {
  const res = await fetch(`${API_BASE}/api/usage/logs${rangeQuery(range, { limit })}`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  const data = await res.json().catch(() => []);
  if (!res.ok) throw new Error(extractErrorMessage(data, "We couldn't load the reply history."));
  return data;
}

/** Downloads a CSV of the chosen view for the period (the browser saves it). */
export async function downloadUsageCsv(
  view: "channel" | "user" | "channel_user" | "logs",
  range?: DateRange,
  workspaceId?: string
): Promise<void> {
  const res = await fetch(
    `${API_BASE}/api/usage/export${rangeQuery(range, { view, workspace_id: workspaceId && workspaceId !== "ALL" ? workspaceId : undefined })}`,
    { headers: getAuthHeaders(), cache: "no-store" }
  );
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(extractErrorMessage(data, "We couldn't create the CSV file."));
  }
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") || "";
  const match = disposition.match(/filename="([^"]+)"/);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = match ? match[1] : `jts-usage-${view}.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export async function recalculateUsageCosts(): Promise<{ status: string; updated_count: number }> {
  const res = await fetch(`${API_BASE}/api/usage/recalculate`, {
    method: "POST",
    headers: getAuthHeaders(),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractErrorMessage(data, "We couldn't recalculate costs."));
  return data;
}

/* ---------------------------------- Invoices ---------------------------------- */

async function invoiceRequest<T>(path: string, init: RequestInit = {}, fallback = "Something went wrong."): Promise<T> {
  const res = await fetch(`${API_BASE}/api/invoices${path}`, {
    ...init,
    headers: getAuthHeaders(init.body ? { "Content-Type": "application/json" } : {}),
    cache: "no-store",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(extractErrorMessage(data, fallback));
  return data as T;
}

export async function fetchInvoices(status?: string): Promise<Invoice[]> {
  const data = await invoiceRequest<{ invoices: Invoice[] }>(
    status && status !== "all" ? `?status=${encodeURIComponent(status)}` : "",
    {},
    "We couldn't load invoices."
  );
  return data.invoices || [];
}

export async function fetchInvoice(invoiceId: number | string): Promise<Invoice> {
  const data = await invoiceRequest<{ invoice: Invoice }>(`/${encodeURIComponent(String(invoiceId))}`, {}, "We couldn't load this invoice.");
  return data.invoice;
}

export async function previewInvoice(params: {
  folder_id: number;
  period_start: string;
  period_end: string;
  markup_percent: number;
}): Promise<InvoicePreview> {
  const q = new URLSearchParams({
    folder_id: String(params.folder_id),
    period_start: params.period_start,
    period_end: params.period_end,
    markup_percent: String(params.markup_percent || 0),
  });
  return invoiceRequest<InvoicePreview>(`/preview?${q.toString()}`, {}, "We couldn't calculate this invoice.");
}

export async function createInvoice(payload: {
  folder_id: number;
  period_start: string;
  period_end: string;
  organization_id?: number | null;
  markup_percent: number;
  due_days: number;
  notes?: string;
}): Promise<{ invoice: Invoice; message: string }> {
  return invoiceRequest("", { method: "POST", body: JSON.stringify(payload) }, "We couldn't create this invoice.");
}

export async function markInvoicePaid(
  invoiceId: number,
  payload: { paid_on: string; reference?: string }
): Promise<{ invoice: Invoice; message: string }> {
  return invoiceRequest(`/${invoiceId}/mark-paid`, { method: "POST", body: JSON.stringify(payload) }, "We couldn't mark this invoice as paid.");
}

export async function voidInvoice(invoiceId: number, reason?: string): Promise<{ invoice: Invoice; message: string }> {
  return invoiceRequest(`/${invoiceId}/void`, { method: "POST", body: JSON.stringify({ reason }) }, "We couldn't void this invoice.");
}

export async function clearBillingData(): Promise<{ status: string; message: string; deleted_count: number }> {
  const res = await fetch(`${API_BASE}/api/usage/clear`, {
    method: "DELETE",
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to clear billing data" }));
    throw new Error(err.detail || "Failed to clear billing data");
  }
  return res.json();
}

export async function deleteChannelBilling(channelId: string): Promise<{ status: string; message: string; deleted_count: number }> {
  const res = await fetch(`${API_BASE}/api/usage/channel/${encodeURIComponent(channelId)}`, {
    method: "DELETE",
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to delete channel billing data" }));
    throw new Error(err.detail || "Failed to delete channel billing data");
  }
  return res.json();
}

export async function deleteUserBilling(userId: string): Promise<{ status: string; message: string; deleted_count: number }> {
  const res = await fetch(`${API_BASE}/api/usage/user/${encodeURIComponent(userId)}`, {
    method: "DELETE",
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to delete user billing data" }));
    throw new Error(err.detail || "Failed to delete user billing data");
  }
  return res.json();
}

export async function deleteChannelUserBilling(channelId: string, userId: string): Promise<{ status: string; message: string; deleted_count: number }> {
  const res = await fetch(`${API_BASE}/api/usage/channel/${encodeURIComponent(channelId)}/user/${encodeURIComponent(userId)}`, {
    method: "DELETE",
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to delete billing entry" }));
    throw new Error(err.detail || "Failed to delete billing entry");
  }
  return res.json();
}

export async function deleteSingleLog(logId: number): Promise<{ status: string; message: string; deleted_count: number }> {
  const res = await fetch(`${API_BASE}/api/usage/logs/${logId}`, {
    method: "DELETE",
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to delete log entry" }));
    throw new Error(err.detail || "Failed to delete log entry");
  }
  return res.json();
}

export async function deleteTableRow(tableName: string, rowId: string | number): Promise<{ status: string; message: string }> {
  const res = await fetch(`${API_BASE}/database/api/table/${encodeURIComponent(tableName)}/row/${encodeURIComponent(rowId)}`, {
    method: "DELETE",
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to delete database row" }));
    throw new Error(err.detail || "Failed to delete database row");
  }
  return res.json();
}

export async function fetchUsageLogs(limit: number = 100): Promise<ApiUsageLog[]> {
  const res = await fetch(`${API_BASE}/api/usage/logs?limit=${limit}`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) return [];
  return res.json();
}

export async function fetchUsers(): Promise<DashboardUser[]> {
  try {
    let res = await fetch(`${API_BASE}/api/users`, {
      headers: getAuthHeaders(),
      cache: "no-store",
    });
    if (res.status === 404) {
      res = await fetch(`${API_BASE}/api/auth/users`, {
        headers: getAuthHeaders(),
        cache: "no-store",
      });
    }
    if (!res.ok) return [];
    const data = await res.json();
    return data.users || [];
  } catch (err) {
    console.error("fetchUsers error:", err);
    return [];
  }
}

export async function createDashboardUser(payload: {
  name: string;
  username: string;
  email: string;
  password?: string | null;
  role: string;
  timezone?: string | null;
  client_folder_id?: number | null;
  organization_id?: number | null;
}): Promise<{ status: string; user_id: number; message: string; email_sent?: boolean }> {
  // Generate a temporary secure placeholder password so older/newer backend APIs never fail on empty password
  const placeholderPassword =
    typeof window !== "undefined" && window.crypto && window.crypto.randomUUID
      ? `Temp_${window.crypto.randomUUID()}_A1!`
      : `Temp_${Math.random().toString(36).slice(2)}_${Date.now()}_A1!`;

  const requestBody = {
    ...payload,
    password: payload.password ? payload.password : placeholderPassword,
  };
  let res = await fetch(`${API_BASE}/api/users`, {
    method: "POST",
    headers: getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(requestBody),
  });
  if (res.status === 404) {
    res = await fetch(`${API_BASE}/api/auth/users`, {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(requestBody),
    });
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to create user" }));
    throw new Error(extractErrorMessage(err, "Failed to create user"));
  }
  const data = await res.json();
  // If backend didn't send setup email automatically (older backend version), send setup email now
  if (!data.email_sent && data.user_id && !payload.password) {
    try {
      await sendUserSetupEmail(data.user_id);
      data.email_sent = true;
    } catch (emailErr) {
      console.warn("Auto email setup trigger note:", emailErr);
    }
  }
  return data;
}

export async function sendUserSetupEmail(
  userId: number
): Promise<{ status: string; message: string; email_result?: any }> {
  let res = await fetch(`${API_BASE}/api/users/${userId}/send-setup-email`, {
    method: "POST",
    headers: getAuthHeaders(),
  });
  if (res.status === 404) {
    res = await fetch(`${API_BASE}/api/auth/users/${userId}/send-setup-email`, {
      method: "POST",
      headers: getAuthHeaders(),
    });
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to send setup email" }));
    throw new Error(extractErrorMessage(err, "Failed to send setup email"));
  }
  return res.json();
}

export async function verifySetupToken(token: string): Promise<VerifyTokenResponse> {
  try {
    const res = await fetch(`${API_BASE}/api/auth/verify-setup-token?token=${encodeURIComponent(token)}`, {
      cache: "no-store",
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: "Failed to verify token" }));
      return { valid: false, message: extractErrorMessage(err, "Token verification failed") };
    }
    return res.json();
  } catch (err: any) {
    return { valid: false, message: err?.message || "Failed to connect to verification server" };
  }
}

export async function submitSetPassword(payload: {
  token: string;
  password: string;
  confirm_password?: string;
}): Promise<{ status: string; message: string; username?: string }> {
  const res = await fetch(`${API_BASE}/api/auth/set-password`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to set password" }));
    throw new Error(extractErrorMessage(err, "Failed to set password"));
  }
  return res.json();
}

export async function updateDashboardUser(
  userId: number,
  payload: {
    name: string;
    email: string;
    password?: string;
    role: string;
    client_folder_id?: number | null;
    organization_id?: number | null;
  }
): Promise<{ status: string; message: string }> {
  let res = await fetch(`${API_BASE}/api/users/${userId}`, {
    method: "PUT",
    headers: getAuthHeaders({ "Content-Type": "application/json" }),
    body: JSON.stringify(payload),
  });

  if (res.status === 404 || res.status === 405) {
    res = await fetch(`${API_BASE}/api/auth/users/${userId}`, {
      method: "PUT",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    });
  }
  if (res.status === 404 || res.status === 405) {
    res = await fetch(`${API_BASE}/api/users/${userId}`, {
      method: "POST",
      headers: getAuthHeaders({ "Content-Type": "application/json" }),
      body: JSON.stringify(payload),
    });
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to update user" }));
    if (res.status === 405) {
      throw new Error(
        "Backend update endpoint not active. Please pull latest code and restart jts-backend on the server: sudo systemctl restart jts-backend"
      );
    }
    throw new Error(err.detail || "Failed to update user");
  }
  return res.json();
}

export async function deleteDashboardUser(userId: number): Promise<{ status: string; message: string }> {
  let res = await fetch(`${API_BASE}/api/users/${userId}`, {
    method: "DELETE",
    headers: getAuthHeaders(),
  });
  if (res.status === 404) {
    res = await fetch(`${API_BASE}/api/auth/users/${userId}`, {
      method: "DELETE",
      headers: getAuthHeaders(),
    });
  }
  if (!res.ok) throw new Error("Failed to delete user");
  return res.json();
}

export async function fetchChannelMessages(channelId: string, limit: number = 250): Promise<{
  status: string;
  channel_id: string;
  channel_name?: string;
  telemetry?: {
    calls: number;
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    total_cost_usd: number;
  };
  messages: ConversationMessage[];
  total: number;
}> {
  const res = await fetch(`${API_BASE}/api/channels/${encodeURIComponent(channelId)}/messages?limit=${limit}`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to fetch channel messages" }));
    throw new Error(err.detail || "Failed to fetch channel messages");
  }
  return res.json();
}

export async function fetchNextOrgId(): Promise<number> {
  try {
    const res = await fetch(`${API_BASE}/api/organizations/next-id`, {
      headers: getAuthHeaders(),
      cache: "no-store",
    });
    if (!res.ok) return 101;
    const data = await res.json();
    const id = Number(data?.next_id);
    return id && id >= 101 ? id : 101;
  } catch {
    return 101;
  }
}

export async function fetchOrganizations(): Promise<{ organizations: any[]; total: number }> {
  const res = await fetch(`${API_BASE}/api/organizations`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) throw new Error("Failed to fetch organizations");
  return res.json();
}

export async function createOrganization(payload: {
  name: string;
  poc?: string;
  phone?: string;
  email?: string;
  billing_email?: string;
  address?: string;
}): Promise<{ status: string; message: string; organization: any }> {
  const res = await fetch(`${API_BASE}/api/organizations`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeaders(),
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to create organization" }));
    throw new Error(err.detail || "Failed to create organization");
  }
  return res.json();
}

export async function updateOrganization(
  orgId: number,
  payload: {
    name?: string;
    poc?: string;
    phone?: string;
    email?: string;
    billing_email?: string;
    address?: string;
  }
): Promise<{ status: string; message: string; organization: any }> {
  const headers = {
    "Content-Type": "application/json",
    ...getAuthHeaders(),
  };
  const body = JSON.stringify(payload);

  // Attempt 1: PUT /api/organizations/{orgId}
  let res = await fetch(`${API_BASE}/api/organizations/${orgId}`, {
    method: "PUT",
    headers,
    body,
  });

  // Attempt 2: POST /api/organizations/{orgId}/update fallback
  if (!res.ok && res.status === 404) {
    res = await fetch(`${API_BASE}/api/organizations/${orgId}/update`, {
      method: "POST",
      headers,
      body,
    });
  }

  // Attempt 3: POST /api/organizations/{orgId} fallback
  if (!res.ok && res.status === 404) {
    res = await fetch(`${API_BASE}/api/organizations/${orgId}`, {
      method: "POST",
      headers,
      body,
    });
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to update organization" }));
    throw new Error(err.detail || "Failed to update organization");
  }
  return res.json();
}

export async function deleteOrganization(orgId: number): Promise<{ status: string; message: string }> {
  const headers = getAuthHeaders();

  // Attempt 1: DELETE /api/organizations/{orgId}
  let res = await fetch(`${API_BASE}/api/organizations/${orgId}`, {
    method: "DELETE",
    headers,
  });

  // Attempt 2: POST /api/organizations/{orgId}/delete fallback
  if (!res.ok && res.status === 404) {
    res = await fetch(`${API_BASE}/api/organizations/${orgId}/delete`, {
      method: "POST",
      headers,
    });
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to delete organization" }));
    throw new Error(err.detail || "Failed to delete organization");
  }
  return res.json();
}

export async function fetchGlobalSettings(): Promise<GlobalSettingsData> {
  const res = await fetch(`${API_BASE}/api/global-settings`, {
    headers: getAuthHeaders(),
    cache: "no-store",
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to fetch global settings" }));
    throw new Error(extractErrorMessage(err, "Failed to fetch global settings"));
  }
  return res.json();
}

export async function updateGlobalSettings(payload: UpdateGlobalSettingsPayload): Promise<GlobalSettingsData> {
  const res = await fetch(`${API_BASE}/api/global-settings`, {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      ...getAuthHeaders(),
    },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to update global settings" }));
    throw new Error(extractErrorMessage(err, "Failed to update global settings"));
  }
  return res.json();
}

export async function resetGlobalSettings(): Promise<GlobalSettingsData> {
  const res = await fetch(`${API_BASE}/api/global-settings/reset`, {
    method: "POST",
    headers: getAuthHeaders(),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: "Failed to reset global settings" }));
    throw new Error(extractErrorMessage(err, "Failed to reset global settings"));
  }
  return res.json();
}




