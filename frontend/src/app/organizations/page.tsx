"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Building2,
  Plus,
  RefreshCw,
  Mail,
  Phone,
  User,
  MapPin,
  Calendar,
  Loader2,
  Building,
  Pencil,
  Trash2,
  AlertCircle,
  X,
  Check,
} from "lucide-react";
import { useRouter } from "next/navigation";
import { fetchOrganizations, updateOrganization, deleteOrganization } from "@/lib/api";
import { DataTable } from "@/components/DataTable";
import { Organization, formatLocalDateTime } from "@/lib/types";
import { AddOrganizationModal } from "@/components/AddOrganizationModal";
import { PageHeader, Alert, EmptyState, LoadingState, btn } from "@/components/ui";

export default function OrganizationsPage() {
  const router = useRouter();
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [showAddModal, setShowAddModal] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  // Edit Organization State
  const [showEditModal, setShowEditModal] = useState(false);
  const [editingOrg, setEditingOrg] = useState<Organization | null>(null);
  const [editName, setEditName] = useState("");
  const [editPoc, setEditPoc] = useState("");
  const [editPhone, setEditPhone] = useState("");
  const [editEmail, setEditEmail] = useState("");
  const [editBillingEmail, setEditBillingEmail] = useState("");
  const [editAddress, setEditAddress] = useState("");
  const [isSubmittingEdit, setIsSubmittingEdit] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);

  // Deleting state
  const [deletingId, setDeletingId] = useState<number | null>(null);

  const loadData = useCallback(async (isRefresh = false) => {
    if (isRefresh) setRefreshing(true);
    else setLoading(true);

    try {
      const res = await fetchOrganizations();
      const orgs = (res.organizations || []).map((o: any) => ({
        ...o,
        id: o.id && o.id < 101 ? o.id + 100 : o.id,
      }));
      setOrganizations(orgs);
    } catch (err: any) {
      console.error("Failed to load organizations:", err);
      setFeedback({ type: "error", message: err.message || "Failed to load organizations." });
    } finally {
      setLoading(false);
      setRefreshing(false);
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

  // Open Edit Modal
  const handleOpenEdit = (org: Organization) => {
    setEditingOrg(org);
    setEditName(org.name || "");
    setEditPoc(org.poc || "");
    setEditPhone(org.phone || "");
    setEditEmail(org.email || "");
    setEditBillingEmail(org.billing_email || "");
    setEditAddress(org.address || "");
    setEditError(null);
    setShowEditModal(true);
  };

  // Submit Edit Organization
  const handleSaveEdit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingOrg) return;

    if (!editName.trim()) {
      setEditError("Please enter the organization name.");
      return;
    }

    if (editEmail.trim()) {
      const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      if (!emailRegex.test(editEmail.trim())) {
        setEditError("Please enter a valid Primary Email address.");
        return;
      }
    }

    if (editBillingEmail.trim()) {
      const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      if (!emailRegex.test(editBillingEmail.trim())) {
        setEditError("Please enter a valid Billing Email address.");
        return;
      }
    }

    setIsSubmittingEdit(true);
    setEditError(null);
    try {
      const targetOrgId = editingOrg.id && editingOrg.id < 101 ? editingOrg.id + 100 : editingOrg.id;
      const res = await updateOrganization(targetOrgId, {
        name: editName.trim(),
        poc: editPoc.trim() || undefined,
        phone: editPhone.trim() || undefined,
        email: editEmail.trim() || undefined,
        billing_email: editBillingEmail.trim() || undefined,
        address: editAddress.trim() || undefined,
      });

      setShowEditModal(false);
      setFeedback({ type: "success", message: res.message || `Organization '${editName}' updated successfully.` });
      await loadData(true);
    } catch (err: any) {
      setEditError(err.message || "Failed to update organization.");
    } finally {
      setIsSubmittingEdit(false);
    }
  };

  // Delete Organization
  const handleDeleteOrg = async (org: Organization) => {
    if (!confirm(`Delete '${org.name}'? This cannot be undone.`)) {
      return;
    }

    setDeletingId(org.id);
    try {
      const res = await deleteOrganization(org.id);
      setFeedback({ type: "success", message: res.message || `Organization '${org.name}' deleted successfully.` });
      await loadData(true);
    } catch (err: any) {
      setFeedback({ type: "error", message: err.message || "Failed to delete organization." });
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="space-y-6 max-w-7xl mx-auto flex flex-col min-h-[calc(100vh-8rem)]">
      <PageHeader
        icon={Building2}
        title="Organizations"
        description="The companies you work with: their contact person, email for invoices and address."
        actions={
          <>
            <button onClick={() => loadData(true)} disabled={loading || refreshing} className={btn.secondary}>
              <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
              <span>Refresh</span>
            </button>
            <button onClick={() => setShowAddModal(true)} className={btn.primary}>
              <Plus className="h-4 w-4" />
              <span>Add organization</span>
            </button>
          </>
        }
      />

      {feedback && (
        <Alert type={feedback.type} onClose={() => setFeedback(null)}>
          {feedback.message}
        </Alert>
      )}

      {/* Organizations Table */}
      <div className="bg-white border border-gray-200 rounded-xl shadow-xs overflow-hidden flex-1 flex flex-col">
        {loading ? (
          <LoadingState label="Loading organizations..." />
        ) : organizations.length === 0 ? (
          <EmptyState
            icon={Building}
            title="No organizations yet"
            description="Add your first client organization to start linking users and Slack channels to it."
            action={
              <button onClick={() => setShowAddModal(true)} className={btn.primary}>
                <Plus className="h-4 w-4" />
                <span>Add organization</span>
              </button>
            }
          />
        ) : (
          <DataTable
            rows={organizations}
            rowKey={(org) => org.id}
            itemLabel="organizations"
            searchPlaceholder="Search name, contact, email or phone"
            initialSort={{ key: "name", dir: "asc" }}
            columns={[
              {
                key: "id",
                header: "ID",
                headerClassName: "w-20",
                render: (org) => (
                  <span className="px-2.5 py-1 rounded-lg bg-gray-100 text-gray-800 border border-gray-200 font-mono text-xs font-bold">
                    {org.id}
                  </span>
                ),
              },
              {
                key: "name",
                header: "Organization",
                sortValue: (org) => (org.name || "").toLowerCase(),
                render: (org) => (
                  <div className="flex items-center gap-2">
                    <div className="h-7 w-7 rounded-lg bg-blue-50 text-[#088ADA] flex items-center justify-center border border-blue-100 shrink-0">
                      <Building2 className="h-3.5 w-3.5" />
                    </div>
                    <span className="text-sm font-bold text-gray-900">{org.name}</span>
                  </div>
                ),
              },
              {
                key: "poc",
                header: "Contact person",
                sortValue: (org) => (org.poc || "").toLowerCase(),
                searchValue: (org) => `${org.poc || ""} ${org.email || ""}`,
                render: (org) => (
                  <div className="space-y-0.5">
                    {org.poc ? (
                      <div className="flex items-center gap-1.5 font-medium text-gray-800">
                        <User className="h-3 w-3 text-gray-400 shrink-0" />
                        <span>{org.poc}</span>
                      </div>
                    ) : (
                      <span className="text-gray-400 italic">Not set</span>
                    )}
                    {org.email && (
                      <div className="flex items-center gap-1.5 text-[11px] text-gray-500 font-mono">
                        <Mail className="h-3 w-3 text-gray-400 shrink-0" />
                        <span>{org.email}</span>
                      </div>
                    )}
                  </div>
                ),
              },
              {
                key: "billing_email",
                header: "Invoice email",
                className: "font-mono text-[11px] text-gray-600",
                render: (org) =>
                  org.billing_email ? (
                    <div className="flex items-center gap-1.5">
                      <Mail className="h-3 w-3 text-emerald-500 shrink-0" />
                      <span>{org.billing_email}</span>
                    </div>
                  ) : (
                    <span className="text-gray-400 italic font-sans">Same as contact email</span>
                  ),
              },
              {
                key: "phone",
                header: "Phone",
                className: "font-mono text-[11px] text-gray-600",
                render: (org) =>
                  org.phone ? (
                    <div className="flex items-center gap-1.5">
                      <Phone className="h-3 w-3 text-gray-400 shrink-0" />
                      <span>{org.phone}</span>
                    </div>
                  ) : (
                    <span className="text-gray-400">—</span>
                  ),
              },
              {
                key: "address",
                header: "Address",
                searchValue: () => "",
                className: "text-gray-600 max-w-xs",
                render: (org) =>
                  org.address ? (
                    <div className="flex items-center gap-1.5" title={org.address}>
                      <MapPin className="h-3 w-3 text-gray-400 shrink-0" />
                      <span className="truncate">{org.address}</span>
                    </div>
                  ) : (
                    <span className="text-gray-400">—</span>
                  ),
              },
              {
                key: "created_at",
                header: "Added on",
                sortValue: (org) => (org.created_at ? new Date(org.created_at).getTime() : null),
                searchValue: () => "",
                className: "font-mono text-[11px] text-gray-500 whitespace-nowrap",
                render: (org) =>
                  org.created_at ? (
                    <div className="flex items-center gap-1.5">
                      <Calendar className="h-3 w-3 text-gray-400 shrink-0" />
                      <span>{formatLocalDateTime(org.created_at)}</span>
                    </div>
                  ) : (
                    "—"
                  ),
              },
              {
                key: "actions",
                header: "Actions",
                sortable: false,
                searchValue: () => "",
                align: "right",
                headerClassName: "w-28",
                render: (org) => (
                  <div className="flex items-center justify-end gap-1.5">
                    <button
                      onClick={() => handleOpenEdit(org)}
                      className="p-1.5 rounded-lg text-gray-500 hover:text-[#088ADA] hover:bg-blue-50 transition"
                      title="Edit organization"
                    >
                      <Pencil className="h-4 w-4" />
                    </button>
                    <button
                      onClick={() => handleDeleteOrg(org)}
                      disabled={deletingId === org.id}
                      className="p-1.5 rounded-lg text-gray-500 hover:text-rose-600 hover:bg-rose-50 transition disabled:opacity-50"
                      title="Delete organization"
                    >
                      {deletingId === org.id ? (
                        <Loader2 className="h-4 w-4 animate-spin text-rose-500" />
                      ) : (
                        <Trash2 className="h-4 w-4" />
                      )}
                    </button>
                  </div>
                ),
              },
            ]}
          />
        )}
      </div>

      {/* Add Organization Modal */}
      <AddOrganizationModal
        isOpen={showAddModal}
        onClose={() => setShowAddModal(false)}
        onSuccess={() => {
          setFeedback({ type: "success", message: "Organization created successfully!" });
          loadData(true);
        }}
      />

      {/* Edit Organization Modal */}
      {showEditModal && editingOrg && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-xs p-4 overflow-y-auto animate-in fade-in">
          <div className="bg-white rounded-2xl shadow-2xl border border-gray-100 w-full max-w-xl overflow-hidden animate-in zoom-in-95 duration-150 my-8">
            {/* Modal Header */}
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 bg-gray-50/50">
              <div className="flex items-center gap-3">
                <div className="h-10 w-10 rounded-xl bg-blue-50 text-[#088ADA] flex items-center justify-center font-bold">
                  <Building2 className="h-5 w-5 text-[#088ADA]" />
                </div>
                <div>
                  <h3 className="text-base font-semibold text-gray-900">Edit organization</h3>
                  <p className="text-xs text-gray-500">Update organization details for ID: {editingOrg.id && editingOrg.id < 101 ? editingOrg.id + 100 : editingOrg.id}</p>
                </div>
              </div>
              <button
                onClick={() => setShowEditModal(false)}
                disabled={isSubmittingEdit}
                className="text-gray-400 hover:text-gray-600 p-1.5 rounded-lg hover:bg-gray-100 transition-colors disabled:opacity-50 cursor-pointer"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            {/* Modal Form */}
            <form onSubmit={handleSaveEdit} className="p-6 space-y-4">
              {editError && (
                <div className="flex items-start gap-2.5 p-3.5 rounded-xl bg-rose-50 border border-rose-200 text-rose-700 text-xs">
                  <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
                  <div className="leading-relaxed">{editError}</div>
                </div>
              )}

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {/* 1. Org ID */}
                <div>
                  <label className="block text-xs font-semibold text-gray-700 mb-1">
                    Org ID <span className="text-rose-500">*</span>
                  </label>
                  <input
                    type="text"
                    disabled
                    value={editingOrg.id && editingOrg.id < 101 ? editingOrg.id + 100 : editingOrg.id}
                    style={{ color: "#374151", backgroundColor: "#f3f4f6" }}
                    className="w-full px-3.5 py-2.5 text-xs bg-gray-100 border border-gray-200 rounded-xl font-mono font-medium cursor-not-allowed select-none"
                  />
                </div>

                {/* 2. Organization Name */}
                <div>
                  <label className="block text-xs font-semibold text-gray-700 mb-1">
                    Organization Name <span className="text-rose-500">*</span>
                  </label>
                  <input
                    type="text"
                    required
                    placeholder="e.g. Acme Corporation"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    disabled={isSubmittingEdit}
                    style={{ color: "#111827", backgroundColor: "#ffffff" }}
                    className="w-full px-3.5 py-2.5 text-xs bg-white text-gray-900 placeholder:text-gray-400 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-[#088ADA]/20 focus:border-[#088ADA] transition-all font-medium"
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {/* 3. POC */}
                <div>
                  <label className="block text-xs font-semibold text-gray-700 mb-1">
                    POC (Point of Contact)
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. Jane Doe"
                    value={editPoc}
                    onChange={(e) => setEditPoc(e.target.value)}
                    disabled={isSubmittingEdit}
                    style={{ color: "#111827", backgroundColor: "#ffffff" }}
                    className="w-full px-3.5 py-2.5 text-xs bg-white text-gray-900 placeholder:text-gray-400 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-[#088ADA]/20 focus:border-[#088ADA] transition-all"
                  />
                </div>

                {/* 4. Phone */}
                <div>
                  <label className="block text-xs font-semibold text-gray-700 mb-1">
                    Phone
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. +1 (555) 019-2834"
                    value={editPhone}
                    onChange={(e) => setEditPhone(e.target.value)}
                    disabled={isSubmittingEdit}
                    style={{ color: "#111827", backgroundColor: "#ffffff" }}
                    className="w-full px-3.5 py-2.5 text-xs bg-white text-gray-900 placeholder:text-gray-400 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-[#088ADA]/20 focus:border-[#088ADA] transition-all"
                  />
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {/* 5. Primary Email */}
                <div>
                  <label className="block text-xs font-semibold text-gray-700 mb-1">
                    Email
                  </label>
                  <input
                    type="email"
                    placeholder="contact@organization.com"
                    value={editEmail}
                    onChange={(e) => setEditEmail(e.target.value)}
                    disabled={isSubmittingEdit}
                    style={{ color: "#111827", backgroundColor: "#ffffff" }}
                    className="w-full px-3.5 py-2.5 text-xs bg-white text-gray-900 placeholder:text-gray-400 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-[#088ADA]/20 focus:border-[#088ADA] transition-all"
                  />
                </div>

                {/* 6. Billing Email */}
                <div>
                  <label className="block text-xs font-semibold text-gray-700 mb-1">
                    Billing Email
                  </label>
                  <input
                    type="email"
                    placeholder="billing@organization.com"
                    value={editBillingEmail}
                    onChange={(e) => setEditBillingEmail(e.target.value)}
                    disabled={isSubmittingEdit}
                    style={{ color: "#111827", backgroundColor: "#ffffff" }}
                    className="w-full px-3.5 py-2.5 text-xs bg-white text-gray-900 placeholder:text-gray-400 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-[#088ADA]/20 focus:border-[#088ADA] transition-all"
                  />
                </div>
              </div>

              {/* 7. Address */}
              <div>
                <label className="block text-xs font-semibold text-gray-700 mb-1">
                  Address
                </label>
                <textarea
                  rows={2}
                  placeholder="e.g. 742 Evergreen Terrace, Suite 100, Springfield, OR 97477"
                  value={editAddress}
                  onChange={(e) => setEditAddress(e.target.value)}
                  disabled={isSubmittingEdit}
                  style={{ color: "#111827", backgroundColor: "#ffffff" }}
                  className="w-full px-3.5 py-2.5 text-xs bg-white text-gray-900 placeholder:text-gray-400 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-[#088ADA]/20 focus:border-[#088ADA] transition-all resize-none"
                />
              </div>

              {/* Form Buttons */}
              <div className="flex items-center justify-end gap-3 pt-3 border-t border-gray-100">
                <button
                  type="button"
                  onClick={() => setShowEditModal(false)}
                  disabled={isSubmittingEdit}
                  className="px-4 py-2 rounded-xl text-xs font-semibold text-gray-700 hover:bg-gray-100 transition-colors disabled:opacity-50 cursor-pointer"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={isSubmittingEdit}
                  className="flex items-center gap-2 px-5 py-2 rounded-xl text-xs font-bold text-white bg-[#088ADA] hover:bg-[#0779c0] active:scale-95 shadow-sm hover:shadow transition-all disabled:opacity-50 cursor-pointer"
                >
                  {isSubmittingEdit ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      <span>Saving...</span>
                    </>
                  ) : (
                    <>
                      <Check className="h-3.5 w-3.5" />
                      <span>Save changes</span>
                    </>
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}
