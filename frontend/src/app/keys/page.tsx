'use client';

import { useEffect, useState, useCallback } from "react";
import {
  KeyRound,
  Lock,
  Plus,
  Trash2,
  RefreshCw,
  Loader2,
  Eye,
  EyeOff,
  ShieldCheck,
} from "lucide-react";
import { PageHeader, Alert, EmptyState, LoadingState, Section, btn, tbl } from "@/components/ui";
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
      <PageHeader
        icon={KeyRound}
        title="API keys"
        description="Keys and tokens the system uses to connect to Anthropic, Slack, GitHub and other services."
        badge={
          <span className="px-2 py-0.5 rounded-full text-[11px] font-medium bg-emerald-50 text-emerald-700 border border-emerald-200 inline-flex items-center gap-1">
            <ShieldCheck className="h-3 w-3" />
            Stored encrypted
          </span>
        }
        actions={
          <button onClick={() => loadData()} disabled={loading} className={btn.secondary}>
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
            <span>Refresh</span>
          </button>
        }
      />

      {feedback && (
        <Alert type={feedback.type} onClose={() => setFeedback(null)}>
          {feedback.message}
        </Alert>
      )}

      {/* Section 1: Add New Secret Key Form */}
      <div className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm space-y-4">
        <div className="flex items-center gap-2 pb-3 border-b border-gray-100">
          <div className="h-8 w-8 rounded-lg bg-[#088ADA]/10 flex items-center justify-center text-[#088ADA]">
            <Lock className="h-4 w-4" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-gray-800">Add a key</h2>
            <p className="text-xs text-gray-500">
              The value is stored securely in AWS and can&apos;t be viewed again after saving. Use the same name to replace an existing key.
            </p>
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
                  <span>Save key</span>
                </>
              )}
            </button>
          </div>
        </form>
      </div>

      {/* Section 2: Stored Secrets Table */}
      <Section
        icon={Lock}
        title={`Saved keys (${vaultSecrets.length})`}
        description="Only the names are shown. Values are never displayed."
        bodyClassName="p-5"
      >
        {loading ? (
          <LoadingState label="Loading keys..." />
        ) : vaultSecrets.length === 0 ? (
          <EmptyState icon={Lock} title="No keys saved yet" description="Add your first API key or token using the form above." />
        ) : (
          <div className={tbl.wrap}>
            <table className={tbl.table}>
              <thead className={tbl.head}>
                <tr>
                  <th className={tbl.th}>Key name</th>
                  <th className={tbl.th}>Stored at</th>
                  <th className={tbl.th}>Added by</th>
                  <th className={`${tbl.th} text-center`}>Date added</th>
                  <th className={`${tbl.th} text-right`}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {vaultSecrets.map((secret) => (
                  <tr key={secret.id} className={tbl.row}>
                    <td className={tbl.td}>
                      <div className="flex items-center gap-2.5">
                        <div className="h-7 w-7 rounded-lg bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA] shrink-0">
                          <KeyRound className="h-3.5 w-3.5" />
                        </div>
                        <span className="text-sm font-semibold text-gray-800">{secret.key_name}</span>
                      </div>
                    </td>
                    <td className={tbl.td}>
                      <span className="font-mono text-[11px] text-emerald-600 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded-md">
                        {secret.aws_secret_name}
                      </span>
                    </td>
                    <td className={`${tbl.td} text-xs font-medium`}>
                      {secret.name || secret.full_name || "JTS Admin"}
                    </td>
                    <td className={`${tbl.td} text-center text-xs text-gray-600`}>
                      {formatLocalDateTime(secret.created_at)}
                    </td>
                    <td className={`${tbl.td} text-right`}>
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
      </Section>
    </div>
  );
}
