"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import QRCode from "qrcode";
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
  Smartphone,
  Copy,
  Check,
} from "lucide-react";
import { login, mfaEnable, mfaSetup, mfaVerify } from "@/lib/api";
import { LoginResponse } from "@/lib/types";

type Step = "password" | "code" | "setup" | "recovery";

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Second step (admins): two-step verification
  const [step, setStep] = useState<Step>("password");
  const [mfaToken, setMfaToken] = useState("");
  const [code, setCode] = useState("");
  const [setupSecret, setSetupSecret] = useState("");
  const [setupUri, setSetupUri] = useState("");
  const [qrImage, setQrImage] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[]>([]);
  const [finished, setFinished] = useState<LoginResponse | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!setupUri) return;
    QRCode.toDataURL(setupUri, { width: 192, margin: 1 })
      .then(setQrImage)
      .catch(() => setQrImage(""));
  }, [setupUri]);

  function goNext(res: LoginResponse) {
    if (typeof window !== "undefined") {
      sessionStorage.removeItem("jts_simulated_role");
    }
    let next = "";
    try {
      next = sessionStorage.getItem("jts_next") || "";
      sessionStorage.removeItem("jts_next");
    } catch {}
    if (next.startsWith("/") && !next.startsWith("//") && !next.startsWith("/login")) {
      router.push(next);
    } else if (res.user?.role === "client_standard") {
      router.push("/my-tasks");
    } else {
      router.push("/");
    }
    router.refresh();
  }

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
      if (res.status === "mfa_required") {
        setMfaToken((res as { mfa_token: string }).mfa_token);
        setCode("");
        setStep("code");
      } else if (res.status === "mfa_setup_required") {
        const token = (res as { mfa_token: string }).mfa_token;
        setMfaToken(token);
        const s = await mfaSetup(token);
        setSetupSecret(s.secret);
        setSetupUri(s.otpauth_uri);
        setCode("");
        setStep("setup");
      } else {
        goNext(res as LoginResponse);
      }
    } catch (err: any) {
      setError(err?.message || "Invalid User ID or Password.");
    } finally {
      setLoading(false);
    }
  }

  async function handleCode(e: React.FormEvent) {
    e.preventDefault();
    if (!code.trim()) {
      setError("Please enter the code.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      if (step === "setup") {
        const res = await mfaEnable(mfaToken, code.trim());
        setFinished(res);
        setRecoveryCodes(res.recovery_codes || []);
        setStep("recovery");
      } else {
        goNext(await mfaVerify(mfaToken, code.trim()));
      }
    } catch (err: any) {
      setError(err?.message || "That code didn't work. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  function backToPassword() {
    setStep("password");
    setMfaToken("");
    setCode("");
    setError(null);
  }

  async function copyCodes() {
    try {
      await navigator.clipboard.writeText(recoveryCodes.join("\n"));
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {}
  }

  const inputBase =
    "w-full py-2.5 bg-white border border-gray-300 rounded-xl text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:ring-2 focus:ring-[#088ADA]/20 transition";

  return (
    <div className="min-h-screen bg-gradient-to-br from-[#e8f4fc] via-[#f5f7fa] to-white flex flex-col justify-center items-center p-4">
      <div className="w-full max-w-sm bg-white border border-gray-200 rounded-2xl p-8 shadow-xl space-y-6">
        {/* Brand & Title */}
        <div className="text-center space-y-3">
          <div className="inline-flex h-12 w-12 rounded-xl bg-[#088ADA] items-center justify-center shadow-md">
            <Terminal className="h-6 w-6 text-white" />
          </div>
          <div>
            <h1 className="text-xl font-semibold text-gray-900">
              {step === "password" ? "Welcome back" : step === "code" ? "Two-step verification" : step === "setup" ? "Set up two-step verification" : "Save your recovery codes"}
            </h1>
            <p className="text-sm text-gray-500 mt-1">
              {step === "password"
                ? "Sign in to JTS PowerTool"
                : step === "code"
                ? "Enter the 6-digit code from your authenticator app"
                : step === "setup"
                ? "Admins protect their account with a code from their phone"
                : "Use one if you ever lose your phone. Each works once."}
            </p>
          </div>
        </div>

        {/* Error Alert */}
        {error && (
          <div role="alert" className="p-3 bg-rose-50 border border-rose-200 rounded-xl text-sm text-rose-700 flex items-start gap-2.5">
            <AlertCircle className="h-4 w-4 text-rose-600 shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {step === "password" && (
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
                  className={`${inputBase} pl-9 pr-3`}
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
                  className={`${inputBase} pl-9 pr-10`}
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
        )}

        {(step === "code" || step === "setup") && (
          <form onSubmit={handleCode} className="space-y-4">
            {step === "setup" && (
              <div className="space-y-3">
                <ol className="text-xs text-gray-600 space-y-1 list-decimal list-inside">
                  <li>Install an authenticator app (Google Authenticator, Microsoft Authenticator or Authy).</li>
                  <li>Scan this square, or choose “enter a setup key” and type the key below.</li>
                  <li>Type the 6-digit code the app shows.</li>
                </ol>
                <div className="flex justify-center">
                  {qrImage ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={qrImage} alt="QR code for your authenticator app" width={192} height={192} className="rounded-lg border border-gray-200" />
                  ) : (
                    <div className="h-48 w-48 rounded-lg border border-gray-200 bg-gray-50 flex items-center justify-center">
                      <RefreshCw className="h-5 w-5 text-gray-400 animate-spin" />
                    </div>
                  )}
                </div>
                <div className="text-center">
                  <div className="text-[11px] text-gray-500 mb-0.5">Setup key</div>
                  <code className="text-xs font-mono tracking-wider text-gray-800 bg-gray-50 border border-gray-200 rounded px-2 py-1 break-all">
                    {setupSecret.replace(/(.{4})/g, "$1 ").trim()}
                  </code>
                </div>
              </div>
            )}

            <div>
              <label htmlFor="login-code" className="block text-sm font-medium text-gray-700 mb-1.5">
                {step === "code" ? "6-digit code or recovery code" : "6-digit code"}
              </label>
              <div className="relative">
                <Smartphone className="h-4 w-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
                <input
                  id="login-code"
                  type="text"
                  inputMode={step === "code" ? "text" : "numeric"}
                  required
                  autoFocus
                  autoComplete="one-time-code"
                  placeholder={step === "code" ? "123456" : "123456"}
                  value={code}
                  onChange={(e) => setCode(e.target.value)}
                  className={`${inputBase} pl-9 pr-3 font-mono tracking-widest`}
                />
              </div>
              {step === "code" && (
                <p className="text-[11px] text-gray-500 mt-1.5">Lost your phone? Type one of your recovery codes instead (like a1b2-c3d4).</p>
              )}
            </div>

            <button
              type="submit"
              disabled={loading}
              className="w-full py-2.5 px-4 bg-[#088ADA] hover:bg-[#0778bd] text-white text-sm font-semibold rounded-xl shadow-sm transition flex items-center justify-center gap-2 disabled:opacity-50"
            >
              {loading ? (
                <>
                  <RefreshCw className="h-4 w-4 animate-spin" />
                  <span>Checking...</span>
                </>
              ) : (
                <>
                  <span>{step === "setup" ? "Turn on and continue" : "Verify"}</span>
                  <ArrowRight className="h-4 w-4" />
                </>
              )}
            </button>
            <button type="button" onClick={backToPassword} className="w-full text-xs text-gray-500 hover:text-gray-800">
              Back to sign in
            </button>
          </form>
        )}

        {step === "recovery" && (
          <div className="space-y-4">
            <div className="grid grid-cols-2 gap-2">
              {recoveryCodes.map((c) => (
                <code key={c} className="text-sm font-mono text-center bg-gray-50 border border-gray-200 rounded-lg py-1.5">
                  {c}
                </code>
              ))}
            </div>
            <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg p-2.5">
              These are shown only once. Save them somewhere safe (a password manager is ideal).
            </p>
            <button
              type="button"
              onClick={copyCodes}
              className="w-full py-2 px-4 border border-gray-300 text-gray-700 text-sm font-medium rounded-xl hover:bg-gray-50 transition flex items-center justify-center gap-2"
            >
              {copied ? <Check className="h-4 w-4 text-emerald-600" /> : <Copy className="h-4 w-4" />}
              {copied ? "Copied" : "Copy the codes"}
            </button>
            <button
              type="button"
              onClick={() => finished && goNext(finished)}
              className="w-full py-2.5 px-4 bg-[#088ADA] hover:bg-[#0778bd] text-white text-sm font-semibold rounded-xl shadow-sm transition flex items-center justify-center gap-2"
            >
              <span>I saved them, continue</span>
              <ArrowRight className="h-4 w-4" />
            </button>
          </div>
        )}

        {step === "password" && (
          <div className="pt-4 border-t border-gray-100 text-center text-xs text-gray-500 flex items-center justify-center gap-1.5">
            <ShieldCheck className="h-3.5 w-3.5 text-emerald-600" />
            <span>Forgot your password? Ask your administrator to send a reset link.</span>
          </div>
        )}
      </div>
    </div>
  );
}
