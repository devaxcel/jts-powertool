"use client";

import { useEffect, useState, useCallback } from "react";
import { Users, UserPlus, Trash2, RefreshCw, Shield, Building, Building2, X, Pencil, Mail } from "lucide-react";
import { useRouter } from "next/navigation";
import {
  fetchUsers,
  createDashboardUser,
  updateDashboardUser,
  deleteDashboardUser,
  fetchFolders,
  fetchOrganizations,
  sendUserSetupEmail,
  extractErrorMessage,
} from "@/lib/api";
import { DataTable } from "@/components/DataTable";
import { DashboardUser, ChannelFolder, Organization, formatLocalDateTime } from "@/lib/types";
import { PageHeader, Alert, btn } from "@/components/ui";
import { UserPermissionsTab } from "@/components/UserPermissionsTab";

const ROLE_LABELS: Record<string, string> = {
  jts_admin: "JTS Admin",
  client_admin: "Client Admin",
  client_standard: "Team Member",
};

export default function UsersPage() {
  const router = useRouter();
  const [users, setUsers] = useState<DashboardUser[]>([]);
  const [folders, setFolders] = useState<ChannelFolder[]>([]);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [showEditModal, setShowEditModal] = useState(false);
  const [editingUser, setEditingUser] = useState<DashboardUser | null>(null);
  const [sendingEmailId, setSendingEmailId] = useState<number | null>(null);
  const [errorMsg, setErrorMsg] = useState("");
  const [successMsg, setSuccessMsg] = useState("");
  const [tab, setTab] = useState<"users" | "permissions">("users");

  // Create User Form State
  const [name, setName] = useState("");
  const [username, setUsername] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<"jts_admin" | "client_admin" | "client_standard">("client_admin");
  const [clientFolderId, setClientFolderId] = useState<string>("");
  const [organizationId, setOrganizationId] = useState<string>("");

  // Edit User Form State
  const [editName, setEditName] = useState("");
  const [editUsername, setEditUsername] = useState("");
  const [editEmail, setEditEmail] = useState("");
  const [editPassword, setEditPassword] = useState("");
  const [editRole, setEditRole] = useState<"jts_admin" | "client_admin" | "client_standard">("client_admin");
  const [editClientFolderId, setEditClientFolderId] = useState<string>("");
  const [editOrganizationId, setEditOrganizationId] = useState<string>("");

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [uList, fList, orgsRes] = await Promise.all([
        fetchUsers(),
        fetchFolders(),
        fetchOrganizations().catch(() => ({ organizations: [], total: 0 })),
      ]);
      setUsers(uList);
      setFolders(fList);
      setOrganizations(orgsRes.organizations || []);
    } catch (e) {
      console.error("Failed to load user management data:", e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (typeof window !== "undefined") {
      try {
        const rawUser = sessionStorage.getItem("jts_user");
        const u = JSON.parse(rawUser || "{}");
        const userRole = u.role || "jts_admin";
        if (userRole === "client_admin" || userRole === "client_standard") {
          router.replace("/");
          return;
        }
      } catch {}
    }
    loadData();
  }, [loadData, router]);

  const resetForm = () => {
    setName("");
    setUsername("");
    setEmail("");
    setRole("client_admin");
    setClientFolderId("");
    setOrganizationId("");
    setErrorMsg("");
  };

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg("");
    setSuccessMsg("");

    const cleanName = name.trim();
    const cleanUsername = username.trim();
    const cleanEmail = email.trim();

    if (!cleanName) {
      setErrorMsg("Full Name is required.");
      return;
    }
    if (!cleanUsername) {
      setErrorMsg("Username / User ID is required.");
      return;
    }
    if (!cleanEmail) {
      setErrorMsg("Email Address is required.");
      return;
    }
    if (!cleanEmail.includes("@") || !cleanEmail.includes(".")) {
      setErrorMsg("Please enter a valid email address.");
      return;
    }
    if (role !== "jts_admin" && !clientFolderId) {
      setErrorMsg("Please select a Client Folder to assign to this user.");
      return;
    }
    if (role === "client_admin" && !organizationId) {
      setErrorMsg("Please select an Organization to assign to this Client Admin.");
      return;
    }

    try {
      const defaultTz =
        typeof window !== "undefined"
          ? localStorage.getItem("jts_global_timezone") ||
            sessionStorage.getItem("jts_global_timezone") ||
            "UTC"
          : "UTC";

      const res = await createDashboardUser({
        name: cleanName,
        username: cleanUsername,
        email: cleanEmail,
        role,
        timezone: defaultTz,
        client_folder_id: clientFolderId ? parseInt(clientFolderId, 10) : null,
        organization_id: organizationId ? parseInt(organizationId, 10) : null,
      });
      setSuccessMsg(
        res.message ||
          `User '${cleanName}' (@${cleanUsername}) created successfully! Password setup invitation email sent to ${cleanEmail}.`
      );
      resetForm();
      setShowModal(false);
      loadData();
    } catch (err: any) {
      setErrorMsg(extractErrorMessage(err, "Failed to create user"));
    }
  };

  const handleSendSetupEmail = async (u: DashboardUser) => {
    if (!u.email) {
      alert(`User '${u.name || u.username}' does not have an email address configured.`);
      return;
    }
    if (!confirm(`Send password setup invitation email to ${u.email}?`)) return;

    setSendingEmailId(u.id);
    setErrorMsg("");
    setSuccessMsg("");
    try {
      const res = await sendUserSetupEmail(u.id);
      setSuccessMsg(res.message || `Password setup email sent to ${u.email}!`);
    } catch (err: any) {
      setErrorMsg(extractErrorMessage(err, "Failed to send password setup email"));
    } finally {
      setSendingEmailId(null);
    }
  };

  const handleOpenEditModal = (u: DashboardUser) => {
    setErrorMsg("");
    setSuccessMsg("");
    setEditingUser(u);
    setEditName(u.name || u.username);
    setEditUsername(u.username);
    setEditEmail(u.email || "");
    setEditPassword("");
    setEditRole(u.role === "jts_admin" || u.role === "client_standard" ? u.role : "client_admin");
    setEditClientFolderId(u.client_folder_id ? String(u.client_folder_id) : "");
    setEditOrganizationId(u.organization_id ? String(u.organization_id) : "");
    setShowEditModal(true);
  };

  const handleUpdateUser = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingUser) return;
    setErrorMsg("");
    setSuccessMsg("");

    const cleanName = editName.trim();
    const cleanEmail = editEmail.trim();
    const cleanPassword = editPassword.trim();

    if (!cleanName) {
      setErrorMsg("Full Name is required.");
      return;
    }
    if (!cleanEmail) {
      setErrorMsg("Email Address is required.");
      return;
    }
    if (!cleanEmail.includes("@") || !cleanEmail.includes(".")) {
      setErrorMsg("Please enter a valid email address.");
      return;
    }
    if (editRole !== "jts_admin" && !editClientFolderId) {
      setErrorMsg("Please select a Client Folder to assign to this user.");
      return;
    }
    if (editRole === "client_admin" && !editOrganizationId) {
      setErrorMsg("Please select an Organization to assign to this Client Admin.");
      return;
    }

    try {
      await updateDashboardUser(editingUser.id, {
        name: cleanName,
        email: cleanEmail,
        password: cleanPassword || undefined,
        role: editRole,
        client_folder_id: editClientFolderId ? parseInt(editClientFolderId, 10) : null,
        organization_id: editOrganizationId ? parseInt(editOrganizationId, 10) : null,
      });
      setSuccessMsg(`User '${cleanName}' updated successfully!`);
      setShowEditModal(false);
      setEditingUser(null);
      loadData();
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to update user");
    }
  };

  const handleDeleteUser = async (userId: number, uName: string) => {
    if (!confirm(`Are you sure you want to delete user '${uName}'?`)) return;
    try {
      await deleteDashboardUser(userId);
      setUsers((prev) => prev.filter((u) => u.id !== userId));
    } catch (err: any) {
      alert(err.message || "Failed to delete user");
    }
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto flex flex-col min-h-[calc(100vh-8rem)]">
      <PageHeader
        icon={Users}
        title="Users"
        description="Add people who can sign in, choose what they can see, and link them to a client."
        actions={
          <>
            <button onClick={loadData} className={btn.secondary}>
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              <span>Refresh</span>
            </button>
            <button
              onClick={() => {
                resetForm();
                setShowModal(true);
              }}
              className={btn.primary}
            >
              <UserPlus className="h-4 w-4" />
              <span>Add user</span>
            </button>
          </>
        }
      />

      {successMsg && <Alert type="success">{successMsg}</Alert>}

      {/* Role Summary Badges */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="p-4 rounded-xl bg-white border border-gray-200 shadow-sm flex items-center gap-3">
          <div className="h-10 w-10 rounded-xl bg-gray-100 border border-gray-200 text-[#088ADA] flex items-center justify-center font-bold">
            <Shield className="h-5 w-5" />
          </div>
          <div>
            <div className="text-xs font-semibold text-gray-800">JTS Admin</div>
            <div className="text-[11px] text-gray-500">Full access to every client and setting</div>
          </div>
        </div>

        <div className="p-4 rounded-xl bg-white border border-gray-200 shadow-sm flex items-center gap-3">
          <div className="h-10 w-10 rounded-xl bg-gray-100 border border-gray-200 text-[#088ADA] flex items-center justify-center font-bold">
            <Building className="h-5 w-5" />
          </div>
          <div>
            <div className="text-xs font-semibold text-gray-800">Client Admin</div>
            <div className="text-[11px] text-gray-500">Manages one client: team, billing and API key</div>
          </div>
        </div>

        <div className="p-4 rounded-xl bg-white border border-gray-200 shadow-sm flex items-center gap-3">
          <div className="h-10 w-10 rounded-xl bg-gray-100 border border-gray-200 text-emerald-600 flex items-center justify-center font-bold">
            <Users className="h-5 w-5" />
          </div>
          <div>
            <div className="text-xs font-semibold text-gray-800">Team Member</div>
            <div className="text-[11px] text-gray-500">Sees only their own requests</div>
          </div>
        </div>
      </div>

      {/* Tabs: the people, and what each may ask the assistant to do */}
      <div className="flex items-center gap-1 bg-gray-100 p-1 rounded-lg w-fit text-xs font-bold">
        {([
          { id: "users", label: "Users" },
          { id: "permissions", label: "Permissions" },
        ] as const).map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`px-4 py-1.5 rounded-md font-bold ${tab === t.id ? "bg-white shadow-sm text-gray-900" : "text-gray-600 hover:text-gray-900"}`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "permissions" ? (
        <UserPermissionsTab
          users={users}
          onSaved={(userId, permissions) => setUsers((prev) => prev.map((u) => (u.id === userId ? { ...u, tool_permissions: permissions } : u)))}
        />
      ) : (
        <>
      {/* Users Table */}
      <div className="bg-white border border-gray-200 rounded-xl overflow-hidden shadow-sm">
        <DataTable
          rows={users}
          rowKey={(u) => u.id}
          itemLabel="users"
          searchPlaceholder="Search name, username, email or client"
          emptyMessage="No users yet. Click “Add user” to create the first one."
          initialSort={{ key: "user", dir: "asc" }}
          columns={[
            {
              key: "user",
              header: "User",
              sortValue: (u) => (u.name || u.username || "").toLowerCase(),
              searchValue: (u) => `${u.name || ""} ${u.username || ""} ${u.email || ""}`,
              render: (u) => (
                <>
                  <div className="font-semibold text-gray-800 flex items-center gap-1.5">
                    <span>{u.name || u.username}</span>
                    {u.name && <span className="text-[11px] text-gray-400 font-mono">@{u.username}</span>}
                  </div>
                  {u.email && <div className="text-[11px] text-gray-400">{u.email}</div>}
                </>
              ),
            },
            {
              key: "role",
              header: "Role",
              sortValue: (u) => ROLE_LABELS[u.role] || u.role,
              render: (u) =>
                u.role === "jts_admin" ? (
                  <span className="px-2.5 py-0.5 rounded-full bg-gray-100 text-gray-800 border border-gray-200 font-semibold text-[10px]">
                    JTS Admin
                  </span>
                ) : u.role === "client_admin" ? (
                  <span className="px-2.5 py-0.5 rounded-full bg-gray-100 text-gray-700 border border-gray-200 font-semibold text-[10px]">
                    Client Admin
                  </span>
                ) : (
                  <span className="px-2.5 py-0.5 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 font-semibold text-[10px]">
                    Team Member
                  </span>
                ),
            },
            {
              key: "client",
              header: "Client",
              sortValue: (u) => u.client_folder_name || "",
              searchValue: (u) => `${u.client_folder_name || ""} ${u.organization_name || ""}`,
              render: (u) =>
                u.client_folder_name ? (
                  <div className="space-y-0.5">
                    <span className="font-semibold text-gray-800 flex items-center gap-1">
                      <Building className="h-3.5 w-3.5 text-[#088ADA]" />
                      {u.client_folder_name}
                    </span>
                    {u.organization_name && (
                      <span className="text-[10px] text-gray-500 font-medium flex items-center gap-1">
                        <Building2 className="h-3 w-3 text-emerald-600" />
                        {u.organization_name}
                      </span>
                    )}
                  </div>
                ) : (
                  <span className="text-gray-400 italic">All clients</span>
                ),
            },
            {
              key: "created_at",
              header: "Added on",
              sortValue: (u) => (u.created_at ? new Date(u.created_at).getTime() : null),
              searchValue: () => "",
              className: "text-gray-400 text-[11px] whitespace-nowrap",
              render: (u) => formatLocalDateTime(u.created_at),
            },
            {
              key: "actions",
              header: "Actions",
              sortable: false,
              searchValue: () => "",
              align: "right",
              render: (u) => (
                <div className="flex items-center justify-end gap-1">
                  <button
                    onClick={() => handleSendSetupEmail(u)}
                    disabled={sendingEmailId === u.id}
                    className="p-1.5 rounded text-indigo-600 hover:bg-indigo-50 disabled:opacity-50 transition"
                    title={u.email ? `Send password setup email to ${u.email}` : "No email on file"}
                  >
                    <Mail className={`h-4 w-4 ${sendingEmailId === u.id ? "animate-pulse text-indigo-400" : ""}`} />
                  </button>
                  <button
                    onClick={() => handleOpenEditModal(u)}
                    className="p-1.5 rounded text-[#088ADA] hover:bg-blue-50 transition"
                    title="Edit user"
                  >
                    <Pencil className="h-4 w-4" />
                  </button>
                  <button
                    onClick={() => handleDeleteUser(u.id, u.name || u.username)}
                    className="p-1.5 rounded text-rose-500 hover:bg-rose-50 transition"
                    title="Delete user"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              ),
            },
          ]}
        />
      </div>

      {/* Create User Modal */}
        </>
      )}

      {showModal && (
        <div className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm flex items-center justify-center p-4 overflow-y-auto">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 space-y-4 max-h-[90vh] overflow-y-auto my-auto">
            <div className="flex items-center justify-between border-b pb-3 border-gray-100 sticky top-0 bg-white z-10">
              <h3 className="font-bold text-gray-800 text-sm flex items-center gap-2">
                <UserPlus className="h-4 w-4 text-[#088ADA]" />
                <span>Add a new user</span>
              </h3>
              <button onClick={() => setShowModal(false)} className="text-gray-400 hover:text-gray-600">
                <X className="h-4 w-4" />
              </button>
            </div>

            <form onSubmit={handleCreateUser} className="space-y-3 text-xs">
              <div>
                <label className="block text-gray-600 font-semibold mb-1">
                  Full Name <span className="text-rose-500">*</span>
                </label>
                <input
                  type="text"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="e.g. Alex Johnson"
                  className="w-full p-2 border border-gray-300 rounded-lg text-gray-800 focus:outline-none focus:border-[#088ADA]"
                />
              </div>

              <div>
                <label className="block text-gray-600 font-semibold mb-1">
                  Username / User ID <span className="text-rose-500">*</span>
                </label>
                <input
                  type="text"
                  required
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="e.g. alex_client"
                  className="w-full p-2 border border-gray-300 rounded-lg text-gray-800 focus:outline-none focus:border-[#088ADA]"
                />
              </div>

              <div>
                <label className="block text-gray-600 font-semibold mb-1">
                  Email Address <span className="text-rose-500">*</span>
                </label>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="alex@clientcompany.com"
                  className="w-full p-2 border border-gray-300 rounded-lg text-gray-800 focus:outline-none focus:border-[#088ADA]"
                />
              </div>

              <div className="p-3 bg-blue-50/80 border border-blue-100 rounded-xl flex items-start gap-2.5 text-blue-900">
                <Mail className="h-4 w-4 text-[#088ADA] shrink-0 mt-0.5" />
                <p className="text-[11px] leading-relaxed text-blue-700">
                  A password setup email with a secure 24-hour link will be sent to this email address so the user can set their own password.
                </p>
              </div>

              {role === "client_admin" && (
                <div>
                  <label className="block text-gray-600 font-semibold mb-1">
                    Assign Organization <span className="text-rose-500">*</span>
                  </label>
                  <select
                    value={organizationId}
                    required
                    onChange={(e) => setOrganizationId(e.target.value)}
                    className="w-full p-2 border border-gray-300 rounded-lg text-gray-800 bg-white focus:outline-none focus:border-[#088ADA]"
                  >
                    <option value="">-- Select Organization --</option>
                    {organizations.map((org) => (
                      <option key={org.id} value={org.id}>
                        {org.name}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {role !== "jts_admin" && (
                <div>
                  <label className="block text-gray-600 font-semibold mb-1">
                    Assign Client Folder <span className="text-rose-500">*</span>
                  </label>
                  <select
                    value={clientFolderId}
                    required
                    onChange={(e) => setClientFolderId(e.target.value)}
                    className="w-full p-2 border border-gray-300 rounded-lg text-gray-800 bg-white focus:outline-none focus:border-[#088ADA]"
                  >
                    <option value="">-- Select Client Folder --</option>
                    {folders.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.name}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <div>
                <label className="block text-gray-600 font-semibold mb-1">
                  User Access Role <span className="text-rose-500">*</span>
                </label>
                <select
                  value={role}
                  onChange={(e: any) => setRole(e.target.value)}
                  className="w-full p-2 border border-gray-300 rounded-lg text-gray-800 bg-white focus:outline-none focus:border-[#088ADA]"
                >
                  <option value="client_admin">Client Admin: manages their organization, billing and API key</option>
                  <option value="client_standard">Team Member: sees their own requests only</option>
                  <option value="jts_admin">JTS Admin: full access to everything</option>
                </select>
              </div>


              {errorMsg && (
                <div className="p-2.5 rounded-lg bg-rose-50 text-rose-700 text-xs border border-rose-200">
                  {errorMsg}
                </div>
              )}

              <div className="pt-3 flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={() => {
                    resetForm();
                    setShowModal(false);
                  }}
                  className="px-3 py-1.5 rounded-lg text-gray-500 hover:bg-gray-100 border border-gray-200"
                >
                  Cancel
                </button>

                <button
                  type="submit"
                  className="px-4 py-1.5 rounded-lg bg-[#088ADA] hover:bg-[#0778bd] text-white font-semibold shadow-sm transition active:scale-95"
                >
                  Create User
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Edit User Modal */}
      {showEditModal && editingUser && (
        <div className="fixed inset-0 z-50 bg-black/50 backdrop-blur-sm flex items-center justify-center p-4 overflow-y-auto">
          <div className="bg-white rounded-2xl max-w-md w-full p-6 shadow-2xl border border-gray-200 space-y-4 max-h-[90vh] overflow-y-auto my-auto">
            <div className="flex items-center justify-between border-b pb-3 border-gray-100 sticky top-0 bg-white z-10">
              <h3 className="font-bold text-gray-800 text-sm flex items-center gap-2">
                <Pencil className="h-4 w-4 text-[#088ADA]" />
                <span>Edit user</span>
              </h3>
              <button
                onClick={() => {
                  setShowEditModal(false);
                  setEditingUser(null);
                }}
                className="text-gray-400 hover:text-gray-600"
              >
                <X className="h-4 w-4" />
              </button>
            </div>

            <form onSubmit={handleUpdateUser} className="space-y-3 text-xs">
              <div>
                <label className="block text-gray-600 font-semibold mb-1">
                  Full Name <span className="text-rose-500">*</span>
                </label>
                <input
                  type="text"
                  required
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  placeholder="e.g. Alex Johnson"
                  className="w-full p-2 border border-gray-300 rounded-lg text-gray-800 focus:outline-none focus:border-[#088ADA]"
                />
              </div>

              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-gray-600 font-semibold">Username / User ID</label>
                  <span className="text-[10px] font-medium text-gray-500 bg-gray-100 border border-gray-200 px-1.5 py-0.5 rounded">
                    Not Editable
                  </span>
                </div>
                <input
                  type="text"
                  disabled
                  readOnly
                  value={editUsername}
                  className="w-full p-2 border border-gray-200 bg-gray-100 text-gray-500 rounded-lg cursor-not-allowed font-mono select-none"
                />
              </div>

              <div>
                <label className="block text-gray-600 font-semibold mb-1">
                  Email Address <span className="text-rose-500">*</span>
                </label>
                <input
                  type="email"
                  required
                  value={editEmail}
                  onChange={(e) => setEditEmail(e.target.value)}
                  placeholder="alex@clientcompany.com"
                  className="w-full p-2 border border-gray-300 rounded-lg text-gray-800 focus:outline-none focus:border-[#088ADA]"
                />
              </div>

              <div>
                <label className="block text-gray-600 font-semibold mb-1">
                  New Password <span className="text-gray-400 font-normal">(Leave blank to keep existing)</span>
                </label>
                <input
                  type="password"
                  value={editPassword}
                  onChange={(e) => setEditPassword(e.target.value)}
                  placeholder="•••••••• (Leave blank to keep current password)"
                  className="w-full p-2 border border-gray-300 rounded-lg text-gray-800 focus:outline-none focus:border-[#088ADA]"
                />
              </div>

              {editRole === "client_admin" && (
                <div>
                  <label className="block text-gray-600 font-semibold mb-1">
                    Assign Organization <span className="text-rose-500">*</span>
                  </label>
                  <select
                    value={editOrganizationId}
                    required
                    onChange={(e) => setEditOrganizationId(e.target.value)}
                    className="w-full p-2 border border-gray-300 rounded-lg text-gray-800 bg-white focus:outline-none focus:border-[#088ADA]"
                  >
                    <option value="">-- Select Organization --</option>
                    {organizations.map((org) => (
                      <option key={org.id} value={org.id}>
                        {org.name}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              {editRole !== "jts_admin" && (
                <div>
                  <label className="block text-gray-600 font-semibold mb-1">
                    Assign Client Folder <span className="text-rose-500">*</span>
                  </label>
                  <select
                    value={editClientFolderId}
                    required
                    onChange={(e) => setEditClientFolderId(e.target.value)}
                    className="w-full p-2 border border-gray-300 rounded-lg text-gray-800 bg-white focus:outline-none focus:border-[#088ADA]"
                  >
                    <option value="">-- Select Client Folder --</option>
                    {folders.map((f) => (
                      <option key={f.id} value={f.id}>
                        {f.name}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <div>
                <label className="block text-gray-600 font-semibold mb-1">
                  User Access Role <span className="text-rose-500">*</span>
                </label>
                <select
                  value={editRole}
                  onChange={(e: any) => setEditRole(e.target.value)}
                  className="w-full p-2 border border-gray-300 rounded-lg text-gray-800 bg-white focus:outline-none focus:border-[#088ADA]"
                >
                  <option value="client_admin">Client Admin: manages their organization, billing and API key</option>
                  <option value="client_standard">Team Member: sees their own requests only</option>
                  <option value="jts_admin">JTS Admin: full access to everything</option>
                </select>
              </div>


              {errorMsg && (
                <div className="p-2.5 rounded-lg bg-rose-50 text-rose-700 text-xs border border-rose-200">
                  {errorMsg}
                </div>
              )}

              <div className="pt-3 flex items-center justify-end gap-2">
                <button
                  type="button"
                  onClick={() => {
                    setShowEditModal(false);
                    setEditingUser(null);
                  }}
                  className="px-3 py-1.5 rounded-lg text-gray-500 hover:bg-gray-100 border border-gray-200"
                >
                  Cancel
                </button>

                <button
                  type="submit"
                  className="px-4 py-1.5 rounded-lg bg-[#088ADA] hover:bg-[#0778bd] text-white font-semibold shadow-sm transition active:scale-95"
                >
                  Save Changes
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

    </div>
  );
}
