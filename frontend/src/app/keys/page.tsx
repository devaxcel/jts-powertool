'use client';

import { useEffect, useState, useCallback } from "react";
import {
  KeyRound,
  Lock,
  Plus,
  Trash2,
  RefreshCw,
  CheckCircle2,
  AlertCircle,
  Database,
  X,
  Loader2,
  Radio,
  Eye,
  EyeOff,
  ShieldCheck,
} from "lucide-react";
import {
  fetchVaultSecrets,
  saveVaultSecret,
  deleteVaultSecret,
} from "@/lib/api";
import { VaultSecret, formatLocalDateTime } from "@/lib/types";

export default function KeysVaultPage() {
  const [vaultSecrets, setVaultSecrets] = useState<VaultSecret[]>([]);
  const [loading, setLoading] = useState(true);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  // Form State
  const [secretKeyName, setSecretKeyName] = useState("");
  const [secretKeyValue, setSecretKeyValue] = useState("");
  const [showSecretValue, setShowSecretValue] = useState(false);
  const [savingSecret, setSavingSecret] = useState(false);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const data = await fetchVaultSecrets();
      setVaultSecrets(data);
    } catch (e: any) {
      console.error("Error loading vault secrets:", e);
      setFeedback({ type: "error", message: e?.message || "Failed to load secrets from vault" });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    setSecretKeyName("");
    setSecretKeyValue("");
    loadData();
    const timer = setTimeout(() => {
      setSecretKeyName("");
      setSecretKeyValue("");
    }, 150);
    return () => clearTimeout(timer);
  }, [loadData]);

  async function handleSaveVaultSecret(e: React.FormEvent) {
    e.preventDefault();
    const formattedKeyName = secretKeyName
      .trim()
      .toUpperCase()
      .replace(/\s+/g, "_");

    if (!formattedKeyName || !secretKeyValue.trim()) {
      setFeedback({ type: "error", message: "Both Key Name and Secret Value are required." });
      return;
    }
    setSavingSecret(true);
    try {
      await saveVaultSecret(formattedKeyName, secretKeyValue.trim());
      setFeedback({
        type: "success",
        message: `Secret key '${formattedKeyName}' saved securely in AWS Secrets Manager and registered in Vault.`,
      });
      setSecretKeyName("");
      setSecretKeyValue("");
      setShowSecretValue(false);
      await loadData();
    } catch (err: any) {
      setFeedback({ type: "error", message: err?.message || "Failed to save secret key to vault" });
    } finally {
      setSavingSecret(false);
    }
  }

  async function handleDeleteVaultSecret(secret: VaultSecret) {
    if (!confirm(`Are you sure you want to permanently delete secret key '${secret.key_name}'?`)) {
      return;
    }
    try {
      await deleteVaultSecret(secret.id);
      setFeedback({ type: "success", message: `Secret '${secret.key_name}' removed from vault and AWS Secrets Manager.` });
      await loadData();
    } catch (err: any) {
      setFeedback({ type: "error", message: err?.message || "Failed to delete secret key" });
    }
  }

  return (
    <div className="space-y-6 max-w-7xl mx-auto flex flex-col min-h-[calc(100vh-8rem)]">
      {/* Top Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 shrink-0 pb-4 border-b border-gray-200">
        <div>
          <div className="flex items-center gap-2.5">
            <div className="h-9 w-9 rounded-xl bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA]">
              <KeyRound className="h-5 w-5" />
            </div>
            <div>
              <h1 className="text-xl font-bold text-gray-800 flex items-center gap-2">
                <span>API Keys &amp; Secrets Vault</span>
                <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-50 text-emerald-600 border border-emerald-200 flex items-center gap-1">
                  <ShieldCheck className="h-3 w-3" />
                  IAM Encrypted
                </span>
                <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold bg-gray-100 text-gray-600 border border-gray-200 flex items-center gap-1">
                  <Radio className="h-3 w-3" />
                  AWS Secrets Manager
                </span>
              </h1>
              <p className="text-xs text-gray-500 mt-0.5">
                Store OpenAI API keys, Anthropic keys, GitHub tokens, Jira credentials, or any secret value safely.
              </p>
            </div>
          </div>
        </div>

        {/* Right Action Button */}
        <div className="flex items-center gap-2.5">
          <button
            onClick={() => loadData()}
            disabled={loading}
            className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-medium border border-gray-200 transition disabled:opacity-50"
            title="Reload secrets from database"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            <span>Refresh</span>
          </button>
        </div>
      </div>

      {/* Feedback Banner */}
      {feedback && (
        <div
          className={`flex items-center justify-between p-3.5 rounded-xl border text-xs sm:text-sm transition-all animate-in fade-in slide-in-from-top-1 ${
            feedback.type === "success"
              ? "bg-emerald-50 border-emerald-200 text-emerald-600"
              : "bg-rose-50 border-rose-200 text-rose-600"
          }`}
        >
          <div className="flex items-center gap-2.5">
            {feedback.type === "success" ? (
              <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-500" />
            ) : (
              <AlertCircle className="h-4 w-4 shrink-0 text-rose-500" />
            )}
            <span>{feedback.message}</span>
          </div>
          <button
            onClick={() => setFeedback(null)}
            className="text-gray-400 hover:text-gray-600 p-1 rounded-lg hover:bg-gray-100 transition"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      )}

      {/* Section 1: Add New Secret Key Form */}
      <div className="bg-gray-50 border border-gray-200 rounded-2xl p-5 shadow-sm space-y-4">
        <div className="flex items-center gap-2 pb-3 border-b border-gray-200">
          <div className="h-7 w-7 rounded-lg bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA]">
            <Lock className="h-4 w-4" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-gray-800">Add New Key / Secret</h2>
            <p className="text-[11px] text-gray-500">Key value is encrypted and stored exclusively in AWS Secrets Manager.</p>
          </div>
        </div>

        <form onSubmit={handleSaveVaultSecret} autoComplete="off" data-lpignore="true" className="grid grid-cols-1 sm:grid-cols-12 gap-4 items-end">
          {/* Prevent browser password managers from auto-populating saved admin credentials */}
          <input type="text" name="fake_username_remembered" tabIndex={-1} className="hidden" aria-hidden="true" autoComplete="off" />
          <input type="password" name="fake_password_remembered" tabIndex={-1} className="hidden" aria-hidden="true" autoComplete="off" />

          {/* Key Name Input */}
          <div className="sm:col-span-4">
            <label className="block text-xs font-medium text-gray-700 mb-1.5">
              Key Name <span className="text-rose-500">*</span>
            </label>
            <input
              type="text"
              name="vault_api_key_name_field"
              id="vault_api_key_name_field"
              autoComplete="off"
              data-lpignore="true"
              data-form-type="other"
              required
              value={secretKeyName}
              onChange={(e) => {
                const formatted = e.target.value.toUpperCase().replace(/\s+/g, "_");
                setSecretKeyName(formatted);
              }}
              placeholder="e.g. OPENAI_API_KEY"
              className="w-full px-3.5 py-2.5 bg-white border border-gray-300 rounded-xl text-gray-800 text-xs placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:ring-1 focus:ring-[#088ADA] transition font-mono"
            />
          </div>

          {/* Key Value Input */}
          <div className="sm:col-span-5">
            <label className="block text-xs font-medium text-gray-700 mb-1.5">
              Key Value (Secret) <span className="text-rose-500">*</span>
            </label>
            <div className="relative">
              <input
                type={showSecretValue ? "text" : "password"}
                name="vault_api_secret_token_field"
                id="vault_api_secret_token_field"
                autoComplete="new-password"
                data-lpignore="true"
                data-form-type="other"
                required
                value={secretKeyValue}
                onChange={(e) => setSecretKeyValue(e.target.value)}
                placeholder="e.g. sk-proj-abc123xyz"
                className="w-full pl-3.5 pr-10 py-2.5 bg-white border border-gray-300 rounded-xl text-gray-800 text-xs placeholder-gray-400 focus:outline-none focus:border-[#088ADA] focus:ring-1 focus:ring-[#088ADA] transition font-mono"
              />
              <button
                type="button"
                onClick={() => setShowSecretValue(!showSecretValue)}
                className="absolute right-3 top-3 text-gray-400 hover:text-gray-600 transition"
              >
                {showSecretValue ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />}
              </button>
            </div>
          </div>

          {/* Submit Button */}
          <div className="sm:col-span-3">
            <button
              type="submit"
              disabled={savingSecret || !secretKeyName.trim() || !secretKeyValue.trim()}
              className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-[#088ADA] hover:bg-[#0778bd] text-white text-xs font-semibold rounded-xl shadow-lg shadow-sky-600/20 transition disabled:opacity-50"
            >
              {savingSecret ? (
                <>
                  <Loader2 className="h-4 w-4 animate-spin" />
                  <span>Saving...</span>
                </>
              ) : (
                <>
                  <Plus className="h-4 w-4" />
                  <span>Store Secret Key</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>

      {/* Section 2: Stored Secrets Table */}
      <div className="space-y-3 flex-1">
        <div className="flex items-center justify-between">
          <span className="text-xs font-semibold text-gray-700 uppercase tracking-wider">
            Stored Keys &amp; Secrets ({vaultSecrets.length})
          </span>
          <span className="text-[11px] text-gray-400 flex items-center gap-1">
            <Database className="h-3 w-3 text-emerald-600" />
            Registered in PostgreSQL
          </span>
        </div>

        {loading ? (
          <div className="h-48 flex flex-col items-center justify-center gap-3 text-gray-500">
            <Loader2 className="h-8 w-8 animate-spin text-[#088ADA]" />
            <p className="text-sm">Loading secrets from vault...</p>
          </div>
        ) : vaultSecrets.length === 0 ? (
          <div className="p-8 rounded-2xl border border-dashed border-gray-200 bg-gray-50 text-center">
            <Lock className="h-8 w-8 text-gray-300 mx-auto mb-2" />
            <h3 className="text-xs font-semibold text-gray-700">No Secrets Stored Yet</h3>
            <p className="text-[11px] text-gray-400 mt-0.5">
              Use the form above to add your first API key or token to AWS Secrets Manager.
            </p>
          </div>
        ) : (
          <div className="overflow-x-auto rounded-xl border border-gray-200 shadow-sm">
            <table className="w-full text-left border-collapse">
              <thead className="bg-[#088ADA] text-white text-xs uppercase tracking-wider sticky top-0 z-20 shadow-sm border-b border-gray-300">
                <tr className="bg-[#088ADA]">
                  <th className="p-3 font-semibold bg-[#088ADA] text-white">Key Name</th>
                  <th className="p-3 font-semibold bg-[#088ADA] text-white">AWS Secret Path</th>
                  <th className="p-3 font-semibold bg-[#088ADA] text-white">Full Name</th>
                  <th className="p-3 font-semibold text-center bg-[#088ADA] text-white">Date Added</th>
                  <th className="p-3 font-semibold text-right bg-[#088ADA] text-white">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-200">
                {vaultSecrets.map((secret, idx) => (
                  <tr
                    key={secret.id}
                    className={`transition hover:bg-gray-200 ${idx % 2 === 0 ? "bg-white" : "bg-[#ededed]"}`}
                  >
                    <td className="p-3">
                      <div className="flex items-center gap-2.5">
                        <div className="h-7 w-7 rounded-lg bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA] shrink-0">
                          <KeyRound className="h-3.5 w-3.5" />
                        </div>
                        <span className="text-sm font-semibold text-gray-800">{secret.key_name}</span>
                      </div>
                    </td>
                    <td className="p-3">
                      <span className="font-mono text-[11px] text-emerald-600 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded-md">
                        {secret.aws_secret_name}
                      </span>
                    </td>
                    <td className="p-3 text-xs font-medium text-gray-700">
                      {secret.name || secret.full_name || "JTS Admin"}
                    </td>
                    <td className="p-3 text-center text-xs text-gray-600">
                      {formatLocalDateTime(secret.created_at)}
                    </td>
                    <td className="p-3 text-right">
                      <button
                        onClick={() => handleDeleteVaultSecret(secret)}
                        className="p-1.5 text-gray-500 hover:text-rose-600 hover:bg-rose-100 rounded-lg transition"
                        title="Delete secret key"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
