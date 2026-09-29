"use client";

import { useState, useEffect, Suspense } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import {
  Terminal,
  Lock,
  Eye,
  EyeOff,
  AlertCircle,
  CheckCircle2,
  ArrowRight,
  ShieldCheck,
  RefreshCw,
  UserCheck,
} from "lucide-react";
import { verifySetupToken, submitSetPassword } from "@/lib/api";
import { VerifyTokenResponse } from "@/lib/types";

function SetPasswordContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const token = searchParams.get("token") || "";

  const [verifying, setVerifying] = useState(true);
  const [tokenInfo, setTokenInfo] = useState<VerifyTokenResponse | null>(null);
  const [verifyError, setVerifyError] = useState<string | null>(null);

  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitSuccess, setSubmitSuccess] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  useEffect(() => {
    async function checkToken() {
      if (!token) {
        setVerifying(false);
        setVerifyError("This link is incomplete. Please open the full link from your invitation email, or ask your admin to send a new one.");
        return;
      }

      try {
        setVerifying(true);
        const res = await verifySetupToken(token);
        if (res.valid) {
          setTokenInfo(res);
          setVerifyError(null);
        } else {
          setVerifyError(res.message || "This link has expired or was already used. Please ask your admin to send you a new invitation.");
        }
      } catch (err: any) {
        setVerifyError(err?.message || "We could not check this link right now. Please try again in a moment.");
      } finally {
        setVerifying(false);
      }
    }

    checkToken();
  }, [token]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!password) {
      setSubmitError("Please enter a new password.");
      return;
    }
    if (password.length < 6) {
      setSubmitError("Password must be at least 6 characters long.");
      return;
    }
    if (password !== confirmPassword) {
      setSubmitError("The two passwords are different. Please type the same password twice.");
      return;
    }

    setSubmitting(true);
    setSubmitError(null);

    try {
      await submitSetPassword({
        token,
        password,
        confirm_password: confirmPassword,
      });
      setSubmitSuccess(true);
    } catch (err: any) {
      setSubmitError(err?.message || "We could not save your password. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen bg-gray-50 flex flex-col justify-center items-center p-4 relative overflow-hidden">
      {/* Background glow effects */}
      <div className="absolute top-1/4 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[500px] h-[500px] bg-[#088ADA]/10 rounded-full blur-3xl pointer-events-none" />
      <div className="absolute bottom-1/4 left-1/3 -translate-x-1/2 w-[350px] h-[350px] bg-sky-400/10 rounded-full blur-3xl pointer-events-none" />

      {/* Main Card */}
      <div className="w-full max-w-md bg-white border border-gray-200/90 rounded-2xl p-8 shadow-2xl backdrop-blur-xl relative z-10 space-y-6">
        {/* Brand & Header */}
        <div className="text-center space-y-3">
          <div className="inline-flex h-12 w-12 rounded-xl bg-gradient-to-tr from-[#088ADA] to-sky-500 items-center justify-center shadow-lg shadow-[#088ADA]/30">
            <Terminal className="h-6 w-6 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-gray-800 tracking-tight">
              Create your password
            </h1>
            <p className="text-xs text-gray-500 mt-1">
              Choose a password you will use to sign in to JTS PowerTool.
            </p>
          </div>
        </div>

        {/* Loading State */}
        {verifying && (
          <div className="py-8 flex flex-col items-center justify-center gap-3 text-center">
            <RefreshCw className="h-7 w-7 text-[#088ADA] animate-spin" />
            <p className="text-xs font-medium text-gray-500">Checking your invitation link...</p>
          </div>
        )}

        {/* Token Invalid / Expired State */}
        {!verifying && verifyError && (
          <div className="space-y-4">
            <div className="p-4 bg-rose-50 border border-rose-200 rounded-xl text-xs text-rose-700 space-y-2">
              <div className="flex items-center gap-2 font-semibold">
                <AlertCircle className="h-4 w-4 text-rose-600 shrink-0" />
                <span>This link no longer works</span>
              </div>
              <p className="text-rose-600/90 leading-relaxed">{verifyError}</p>
            </div>

            <button
              type="button"
              onClick={() => router.push("/login")}
              className="w-full py-2.5 px-4 bg-gray-900 hover:bg-black text-white text-xs font-semibold rounded-xl transition flex items-center justify-center gap-2 shadow"
            >
              <span>Go to sign in</span>
              <ArrowRight className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

        {/* Success State */}
        {!verifying && !verifyError && submitSuccess && (
          <div className="space-y-5 animate-in fade-in zoom-in-95 duration-200">
            <div className="p-4 bg-emerald-50 border border-emerald-200 rounded-xl text-center space-y-2">
              <div className="inline-flex h-10 w-10 rounded-full bg-emerald-100 items-center justify-center text-emerald-600 mb-1">
                <CheckCircle2 className="h-6 w-6" />
              </div>
              <h3 className="text-sm font-bold text-emerald-800">Your password is ready</h3>
              <p className="text-xs text-emerald-700/90">
                You can now sign in with your username or email and this password.
              </p>
            </div>

            <button
              type="button"
              onClick={() => router.push("/login")}
              className="w-full py-2.5 px-4 bg-[#088ADA] hover:bg-[#0778bd] text-white text-xs font-semibold rounded-xl shadow-lg shadow-[#088ADA]/20 transition flex items-center justify-center gap-2"
            >
              <span>Sign in</span>
              <ArrowRight className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

        {/* Password Setup Form */}
        {!verifying && !verifyError && !submitSuccess && (
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* User Details Badge */}
            {tokenInfo && (
              <div className="p-3 bg-sky-50 border border-sky-100 rounded-xl flex items-center gap-3">
                <div className="h-8 w-8 rounded-lg bg-[#088ADA]/10 flex items-center justify-center text-[#088ADA] shrink-0">
                  <UserCheck className="h-4 w-4" />
                </div>
                <div className="min-w-0 text-left">
                  <p className="text-xs font-semibold text-gray-800 truncate">
                    {tokenInfo.name || tokenInfo.username}
                  </p>
                  <p className="text-[11px] text-gray-500 truncate">
                    @{tokenInfo.username} {tokenInfo.email ? `• ${tokenInfo.email}` : ""}
                  </p>
                </div>
              </div>
            )}

            {/* Submit Error Alert */}
            {submitError && (
              <div className="p-3 bg-rose-50 border border-rose-200 rounded-xl text-xs text-rose-700 flex items-center gap-2.5 animate-in fade-in">
                <AlertCircle className="h-4 w-4 text-rose-500 shrink-0" />
                <span>{submitError}</span>
              </div>
            )}

            {/* New Password */}
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1.5">
                New password
              </label>
              <div className="relative">
                <Lock className="h-4 w-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type={showPassword ? "text" : "password"}
                  required
                  autoFocus
                  placeholder="At least 6 characters"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="w-full pl-9 pr-10 py-2 bg-white border border-gray-200 rounded-xl text-xs text-gray-800 placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:ring-2 focus:ring-[#088ADA]/20 transition"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600 transition"
                >
                  {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                </button>
              </div>
            </div>

            {/* Confirm Password */}
            <div>
              <label className="block text-xs font-medium text-gray-700 mb-1.5">
                Type it again
              </label>
              <div className="relative">
                <Lock className="h-4 w-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  type={showPassword ? "text" : "password"}
                  required
                  placeholder="Same password as above"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  className="w-full pl-9 pr-10 py-2 bg-white border border-gray-200 rounded-xl text-xs text-gray-800 placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:ring-2 focus:ring-[#088ADA]/20 transition"
                />
              </div>
            </div>

            {/* Submit Button */}
            <button
              type="submit"
              disabled={submitting}
              className="w-full py-2.5 px-4 bg-[#088ADA] hover:bg-[#0778bd] disabled:opacity-50 text-white text-xs font-semibold rounded-xl shadow-lg shadow-[#088ADA]/20 transition flex items-center justify-center gap-2 mt-2"
            >
              {submitting ? (
                <>
                  <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                  <span>Saving...</span>
                </>
              ) : (
                <>
                  <span>Save password</span>
                  <ArrowRight className="h-3.5 w-3.5" />
                </>
              )}
            </button>
          </form>
        )}

        {/* Footer */}
        <div className="pt-4 border-t border-gray-100 flex items-center justify-between text-[11px] text-gray-400">
          <div className="flex items-center gap-1.5">
            <ShieldCheck className="h-3.5 w-3.5 text-emerald-600" />
            <span>Secure one-time link</span>
          </div>
          <button
            type="button"
            onClick={() => router.push("/login")}
            className="text-[#088ADA] hover:underline font-medium"
          >
            Back to sign in
          </button>
        </div>
      </div>
    </div>
  );
}

export default function SetPasswordPage() {
  return (
    <Suspense
      fallback={
        <div className="min-h-screen bg-gray-50 flex items-center justify-center">
          <RefreshCw className="h-7 w-7 text-[#088ADA] animate-spin" />
        </div>
      }
    >
      <SetPasswordContent />
    </Suspense>
  );
}
