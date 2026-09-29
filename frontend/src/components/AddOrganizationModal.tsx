"use client";

import { useState, useEffect } from "react";
import { Building2, X, CheckCircle2, AlertCircle, Loader2 } from "lucide-react";
import { createOrganization, fetchNextOrgId } from "@/lib/api";

interface AddOrganizationModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess?: () => void;
}

export function AddOrganizationModal({ isOpen, onClose, onSuccess }: AddOrganizationModalProps) {
  const [nextOrgId, setNextOrgId] = useState<number | string>(101);
  const [orgName, setOrgName] = useState("");
  const [poc, setPoc] = useState("");
  const [phone, setPhone] = useState("");
  const [email, setEmail] = useState("");
  const [billingEmail, setBillingEmail] = useState("");
  const [address, setAddress] = useState("");

  const [isLoadingNextId, setIsLoadingNextId] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      setErrorMsg(null);
      setSuccessMsg(null);
      setOrgName("");
      setPoc("");
      setPhone("");
      setEmail("");
      setBillingEmail("");
      setAddress("");
      
      // Fetch preview next Org ID
      setIsLoadingNextId(true);
      fetchNextOrgId()
        .then((id) => setNextOrgId(id && Number(id) >= 101 ? Number(id) : 101))
        .catch(() => setNextOrgId(101))
        .finally(() => setIsLoadingNextId(false));
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg(null);
    setSuccessMsg(null);

    if (!orgName.trim()) {
      setErrorMsg("Organization Name is required.");
      return;
    }

    if (email.trim()) {
      const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      if (!emailRegex.test(email.trim())) {
        setErrorMsg("Please enter a valid Primary Email address.");
        return;
      }
    }

    if (billingEmail.trim()) {
      const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      if (!emailRegex.test(billingEmail.trim())) {
        setErrorMsg("Please enter a valid Billing Email address.");
        return;
      }
    }

    setIsSubmitting(true);
    try {
      const res = await createOrganization({
        name: orgName.trim(),
        poc: poc.trim() || undefined,
        phone: phone.trim() || undefined,
        email: email.trim() || undefined,
        billing_email: billingEmail.trim() || undefined,
        address: address.trim() || undefined,
      });

      setSuccessMsg(res.message || "Organization created successfully!");
      if (onSuccess) onSuccess();

      // Close modal after brief delay
      setTimeout(() => {
        onClose();
      }, 1200);
    } catch (err: any) {
      setErrorMsg(err.message || "Failed to create organization. Please check input fields.");
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 overflow-y-auto">
      <div className="bg-white rounded-2xl shadow-2xl border border-gray-100 w-full max-w-xl overflow-hidden animate-in fade-in zoom-in duration-150 my-8">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-gray-100 bg-gray-50/50">
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-xl bg-[#088ADA]/10 text-[#088ADA] flex items-center justify-center font-bold">
              <Building2 className="h-5 w-5 text-[#088ADA]" />
            </div>
            <div>
              <h3 className="text-base font-semibold text-gray-900">Add New Organization</h3>
              <p className="text-xs text-gray-500">Register a new organization tenant profile in the database</p>
            </div>
          </div>
          <button
            onClick={onClose}
            disabled={isSubmitting}
            className="text-gray-400 hover:text-gray-600 p-1.5 rounded-lg hover:bg-gray-100 transition-colors disabled:opacity-50"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Content & Form */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {errorMsg && (
            <div className="flex items-start gap-2.5 p-3.5 rounded-xl bg-rose-50 border border-rose-200 text-rose-700 text-xs">
              <AlertCircle className="h-4 w-4 shrink-0 mt-0.5" />
              <div className="leading-relaxed">{errorMsg}</div>
            </div>
          )}

          {successMsg && (
            <div className="flex items-center gap-2.5 p-3.5 rounded-xl bg-emerald-50 border border-emerald-200 text-emerald-700 text-xs">
              <CheckCircle2 className="h-4 w-4 shrink-0" />
              <div className="font-medium">{successMsg}</div>
            </div>
          )}

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {/* 1. Org ID */}
            <div>
              <label className="block text-xs font-semibold text-gray-700 mb-1">
                Org ID <span className="text-rose-500">*</span>
              </label>
              <div className="relative">
                <input
                  type="text"
                  disabled
                  value={isLoadingNextId ? "" : `${nextOrgId}`}
                  style={{ color: "#374151", backgroundColor: "#f3f4f6" }}
                  className="w-full px-3.5 py-2.5 text-xs bg-gray-100 border border-gray-200 rounded-xl font-mono font-medium cursor-not-allowed select-none"
                />
              </div>
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
                value={orgName}
                onChange={(e) => setOrgName(e.target.value)}
                disabled={isSubmitting}
                style={{ color: "#111827", backgroundColor: "#ffffff" }}
                className="w-full px-3.5 py-2.5 text-xs bg-white text-gray-900 placeholder:text-gray-400 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-[#088ADA]/20 focus:border-[#088ADA] transition-all font-medium"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {/* 3. POC (if any) */}
            <div>
              <label className="block text-xs font-semibold text-gray-700 mb-1">
                POC (Point of Contact)
              </label>
              <input
                type="text"
                placeholder="e.g. Jane Doe"
                value={poc}
                onChange={(e) => setPoc(e.target.value)}
                disabled={isSubmitting}
                style={{ color: "#111827", backgroundColor: "#ffffff" }}
                className="w-full px-3.5 py-2.5 text-xs bg-white text-gray-900 placeholder:text-gray-400 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-[#088ADA]/20 focus:border-[#088ADA] transition-all font-medium"
              />
            </div>

            {/* 4. Phone */}
            <div>
              <label className="block text-xs font-semibold text-gray-700 mb-1">
                Phone
              </label>
              <input
                type="tel"
                placeholder="e.g. +1 (555) 019-2834"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                disabled={isSubmitting}
                style={{ color: "#111827", backgroundColor: "#ffffff" }}
                className="w-full px-3.5 py-2.5 text-xs bg-white text-gray-900 placeholder:text-gray-400 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-[#088ADA]/20 focus:border-[#088ADA] transition-all font-medium"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            {/* 5. Email */}
            <div>
              <label className="block text-xs font-semibold text-gray-700 mb-1">
                Email
              </label>
              <input
                type="email"
                placeholder="contact@organization.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                disabled={isSubmitting}
                style={{ color: "#111827", backgroundColor: "#ffffff" }}
                className="w-full px-3.5 py-2.5 text-xs bg-white text-gray-900 placeholder:text-gray-400 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-[#088ADA]/20 focus:border-[#088ADA] transition-all font-medium"
              />
              <p className="text-[10px] text-gray-400 mt-1">Unique across all organizations.</p>
            </div>

            {/* 6. Billing Email */}
            <div>
              <label className="block text-xs font-semibold text-gray-700 mb-1">
                Billing Email
              </label>
              <input
                type="email"
                placeholder="billing@organization.com"
                value={billingEmail}
                onChange={(e) => setBillingEmail(e.target.value)}
                disabled={isSubmitting}
                style={{ color: "#111827", backgroundColor: "#ffffff" }}
                className="w-full px-3.5 py-2.5 text-xs bg-white text-gray-900 placeholder:text-gray-400 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-[#088ADA]/20 focus:border-[#088ADA] transition-all font-medium"
              />
              <p className="text-[10px] text-gray-400 mt-1">Can match Primary Email or be distinct.</p>
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
              value={address}
              onChange={(e) => setAddress(e.target.value)}
              disabled={isSubmitting}
              style={{ color: "#111827", backgroundColor: "#ffffff" }}
              className="w-full px-3.5 py-2.5 text-xs bg-white text-gray-900 placeholder:text-gray-400 border border-gray-300 rounded-xl focus:outline-none focus:ring-2 focus:ring-[#088ADA]/20 focus:border-[#088ADA] transition-all resize-none font-medium"
            />
          </div>

          {/* Footer Actions */}
          <div className="flex items-center justify-end gap-3 pt-4 border-t border-gray-100">
            <button
              type="button"
              onClick={onClose}
              disabled={isSubmitting}
              className="px-4 py-2 text-xs font-medium text-gray-600 hover:text-gray-800 bg-gray-100 hover:bg-gray-200 rounded-xl transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting || !!successMsg}
              className="px-5 py-2 text-xs font-semibold text-white bg-[#088ADA] hover:bg-[#0779c0] active:scale-98 rounded-xl shadow-sm hover:shadow transition-all flex items-center gap-2 disabled:opacity-50"
            >
              {isSubmitting ? (
                <>
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  <span>Saving Organization...</span>
                </>
              ) : (
                <>
                  <Building2 className="h-3.5 w-3.5" />
                  <span>Save Organization</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
