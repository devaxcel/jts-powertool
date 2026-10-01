"use client";

import { useCallback, useEffect, useState } from "react";
import { Globe, ExternalLink, GitPullRequest, RefreshCw, Loader2 } from "lucide-react";
import { fetchWebsites, ClientWebsite } from "@/lib/api";
import { formatLocalDateTime } from "@/lib/types";
import { Alert, btn } from "@/components/ui";

const STACK_LABELS: Record<string, string> = {
  static: "Static",
  react: "React",
  vue: "Vue",
  svelte: "Svelte",
  astro: "Astro",
  nextjs: "Next.js",
  php: "PHP · code only",
  laravel: "Laravel · code only",
  wordpress: "WordPress theme",
};

/** Websites the AI built and published for this client. New sites and changes are requested in Slack. */
export function WebsitesCard({ folderId }: { folderId: number | string }) {
  const [sites, setSites] = useState<ClientWebsite[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setSites(await fetchWebsites(Number(folderId)));
      setError(null);
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [folderId]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <section className="bg-white border border-gray-200 rounded-2xl p-5 shadow-sm space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="h-10 w-10 rounded-xl bg-sky-50 text-[#088ADA] flex items-center justify-center shrink-0">
            <Globe className="h-5 w-5" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-gray-800">Websites</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Sites the assistant built and published to this client&apos;s GitHub. To create or change one, ask in Slack, e.g.{" "}
              <span className="font-mono">@bot build a website for…</span> or <span className="font-mono">@bot add a gallery page to our site</span>.
            </p>
          </div>
        </div>
        <button onClick={load} className={btn.secondary} disabled={loading} aria-label="Refresh websites">
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {error && <Alert type="error">{error}</Alert>}

      {loading && !sites ? (
        <p className="text-xs text-gray-500 flex items-center gap-2">
          <Loader2 className="h-3.5 w-3.5 animate-spin" /> Loading websites…
        </p>
      ) : !sites || sites.length === 0 ? (
        <p className="text-xs text-gray-600">No websites yet.</p>
      ) : (
        <ul className="divide-y divide-gray-100">
          {sites.map((w) => (
            <li key={w.repo_full_name} className="py-3 flex flex-col sm:flex-row sm:items-center justify-between gap-2">
              <div className="min-w-0">
                <p className="text-sm font-semibold text-gray-800 truncate">
                  {w.repo_full_name}
                  {w.stack && (
                    <span className="ml-2 align-middle text-[10px] font-semibold px-1.5 py-0.5 rounded-full bg-gray-100 border border-gray-200 text-gray-600">
                      {STACK_LABELS[w.stack] || w.stack}
                    </span>
                  )}
                </p>
                <p className="text-xs text-gray-500">
                  {w.last_change || "Published"} · {w.updated_at ? formatLocalDateTime(w.updated_at) : "—"}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {w.site_url && (
                  <a href={w.site_url} target="_blank" rel="noopener noreferrer" className={btn.primary}>
                    <ExternalLink className="h-3.5 w-3.5" /> Open site
                  </a>
                )}
                <a href={`https://github.com/${w.repo_full_name}`} target="_blank" rel="noopener noreferrer" className={btn.secondary}>
                  Repository
                </a>
                {w.last_pr_url && (
                  <a href={w.last_pr_url} target="_blank" rel="noopener noreferrer" className={btn.secondary} title="Last change (pull request)">
                    <GitPullRequest className="h-3.5 w-3.5" /> Last change
                  </a>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
