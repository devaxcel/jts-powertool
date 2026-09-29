"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import {
  Terminal,
  Lock,
  User,
  Eye,
  EyeOff,
  AlertCircle,
  ArrowRight,
  ShieldCheck,
  RefreshCw,
} from "lucide-react";
import { login } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!username.trim() || !password.trim()) {
      setError("Please enter both User ID and Password.");
      return;
    }

    setLoading(true);
    setError(null);

    try {
      const res = await login(username.trim(), password.trim());
      if (typeof window !== "undefined") {
        sessionStorage.removeItem("jts_simulated_role");
      }
      if (res.user?.role === "client_standard") {
        router.push("/my-tasks");
      } else {
        router.push("/");
      }
      router.refresh();
    } catch (err: any) {
      setError(err?.message || "Invalid User ID or Password.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-[#e8f4fc] via-[#f5f7fa] to-white flex flex-col justify-center items-center p-4">
      <div className="w-full max-w-sm bg-white border border-gray-200 rounded-2xl p-8 shadow-xl space-y-6">
        {/* Brand & Title */}
        <div className="text-center space-y-3">
          <div className="inline-flex h-12 w-12 rounded-xl bg-[#088ADA] items-center justify-center shadow-md">
            <Terminal className="h-6 w-6 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-gray-900">Welcome back</h1>
            <p className="text-sm text-gray-500 mt-1">Sign in to JTS PowerTool</p>
          </div>
        </div>

        {/* Error Alert */}
        {error && (
          <div role="alert" className="p-3 bg-rose-50 border border-rose-200 rounded-xl text-sm text-rose-700 flex items-start gap-2.5">
            <AlertCircle className="h-4 w-4 text-rose-600 shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="login-username" className="block text-sm font-medium text-gray-700 mb-1.5">
              Username or email
            </label>
            <div className="relative">
              <User className="h-4 w-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                id="login-username"
                type="text"
                required
                autoFocus
                autoComplete="username"
                placeholder="you@company.com"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="w-full pl-9 pr-3 py-2.5 bg-white border border-gray-300 rounded-xl text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:ring-2 focus:ring-[#088ADA]/20 transition"
              />
            </div>
          </div>

          <div>
            <label htmlFor="login-password" className="block text-sm font-medium text-gray-700 mb-1.5">
              Password
            </label>
            <div className="relative">
              <Lock className="h-4 w-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
              <input
                id="login-password"
                type={showPassword ? "text" : "password"}
                required
                autoComplete="current-password"
                placeholder="Your password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="w-full pl-9 pr-10 py-2.5 bg-white border border-gray-300 rounded-xl text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:ring-2 focus:ring-[#088ADA]/20 transition"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                aria-label={showPassword ? "Hide password" : "Show password"}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
              >
                {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
              </button>
            </div>
          </div>

          <button
            type="submit"
            disabled={loading}
            className="w-full py-2.5 px-4 bg-[#088ADA] hover:bg-[#0778bd] text-white text-sm font-semibold rounded-xl shadow-sm transition flex items-center justify-center gap-2 disabled:opacity-50"
          >
            {loading ? (
              <>
                <RefreshCw className="h-4 w-4 animate-spin" />
                <span>Signing in...</span>
              </>
            ) : (
              <>
                <span>Sign in</span>
                <ArrowRight className="h-4 w-4" />
              </>
            )}
          </button>
        </form>

        <div className="pt-4 border-t border-gray-100 text-center text-xs text-gray-500 flex items-center justify-center gap-1.5">
          <ShieldCheck className="h-3.5 w-3.5 text-emerald-600" />
          <span>Forgot your password? Ask your administrator to send a reset link.</span>
        </div>
      </div>
    </div>
  );
}
