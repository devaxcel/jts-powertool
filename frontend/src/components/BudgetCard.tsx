"use client";

import { useCallback, useEffect, useState } from "react";
import { Gauge, Loader2, Save } from "lucide-react";
import { BudgetStatus, fetchBudget, saveBudget } from "@/lib/api";
import { Alert, Badge, LoadingState, Section, btn, inputClass } from "@/components/ui";

const usd = (n: number) => `$${n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/** A client's monthly AI budget: how much is used, and (for JTS Admins) the limits and alert levels. */
export function BudgetCard({ folderId }: { folderId: number | string }) {
  const [data, setData] = useState<BudgetStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);
  const [limitUsd, setLimitUsd] = useState("");
  const [limitTokens, setLimitTokens] = useState("");
  const [levels, setLevels] = useState("50, 80, 100");
  const [hardStop, setHardStop] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetchBudget(folderId);
      setData(res);
      setLimitUsd(res.budget.monthly_usd != null ? String(res.budget.monthly_usd) : "");
      setLimitTokens(res.budget.monthly_tokens != null ? String(res.budget.monthly_tokens) : "");
      setLevels(res.budget.thresholds.join(", "));
      setHardStop(res.budget.hard_stop);
    } catch (e: any) {
      setFeedback({ type: "error", message: e.message });
    } finally {
      setLoading(false);
    }
  }, [folderId]);

  useEffect(() => {
    load();
  }, [load]);

  async function handleSave(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setFeedback(null);
    try {
      const res = await saveBudget(folderId, {
        monthly_usd: limitUsd.trim() ? Number(limitUsd) : null,
        monthly_tokens: limitTokens.trim() ? Math.round(Number(limitTokens)) : null,
        thresholds: levels
          .split(/[,\s]+/)
          .map((s) => s.replace("%", "").trim())
          .filter(Boolean)
          .map(Number),
        hard_stop: hardStop,
      });
      setData(res);
      setFeedback({ type: "success", message: "Saved." });
    } catch (e: any) {
      setFeedback({ type: "error", message: e.message });
    } finally {
      setBusy(false);
    }
  }

  if (loading && !data) return <LoadingState label="Loading budget..." />;
  const pct = data?.percent;
  const used = data?.usage;
  const budget = data?.budget;
  const tone = pct == null ? "gray" : pct >= 100 ? "rose" : pct >= 80 ? "amber" : "green";
  const barColor = pct == null ? "bg-gray-300" : pct >= 100 ? "bg-rose-500" : pct >= 80 ? "bg-amber-500" : "bg-[#088ADA]";

  return (
    <Section
      icon={Gauge}
      title={
        <span className="inline-flex items-center gap-2">
          Monthly AI budget
          <Badge tone={tone}>{pct == null ? "No limit set" : `${pct}% used`}</Badge>
        </span>
      }
      description="Only usage billed on the JTS key counts. If the client uses its own Anthropic key, nothing is billed and no budget applies. The month runs from the 1st to the last day (UTC)."
      bodyClassName="p-5 space-y-5"
    >
      {feedback && (
        <Alert type={feedback.type} onClose={() => setFeedback(null)}>
          {feedback.message}
        </Alert>
      )}

      {used && budget && (
        <div className="space-y-2">
          <div className="h-3 rounded-full bg-gray-100 overflow-hidden">
            <div className={`h-full ${barColor} transition-all`} style={{ width: `${Math.min(pct ?? 0, 100)}%` }} />
          </div>
          <dl className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
            <div>
              <dt className="text-gray-500">Spent this month</dt>
              <dd className="font-semibold text-gray-900 mt-0.5">
                {usd(used.used_usd)}
                {budget.monthly_usd != null && <span className="font-normal text-gray-500"> of {usd(budget.monthly_usd)}</span>}
              </dd>
            </div>
            <div>
              <dt className="text-gray-500">Tokens used</dt>
              <dd className="font-semibold text-gray-900 mt-0.5">
                {used.used_tokens.toLocaleString()}
                {budget.monthly_tokens != null && <span className="font-normal text-gray-500"> of {budget.monthly_tokens.toLocaleString()}</span>}
              </dd>
            </div>
            <div>
              <dt className="text-gray-500">AI replies</dt>
              <dd className="font-semibold text-gray-900 mt-0.5">{used.replies.toLocaleString()}</dd>
            </div>
          </dl>
          {budget.configured && (
            <p className="text-[11px] text-gray-500">
              Alerts go to the JTS admins and this client&apos;s admins at {budget.thresholds.join("%, ")}%.{" "}
              {budget.hard_stop ? "The assistant stops answering when the limit is reached." : "The assistant keeps working past the limit; the extra usage is still billed."}
            </p>
          )}
        </div>
      )}

      {data?.can_edit ? (
        <form onSubmit={handleSave} className="space-y-4 pt-4 border-t border-gray-100">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <label className="block space-y-1">
              <span className="text-xs font-semibold text-gray-700">Monthly limit in dollars</span>
              <input type="number" min="0" step="0.01" value={limitUsd} onChange={(e) => setLimitUsd(e.target.value)} placeholder="e.g. 50 (empty = no dollar limit)" className={inputClass} />
            </label>
            <label className="block space-y-1">
              <span className="text-xs font-semibold text-gray-700">Monthly limit in tokens (optional)</span>
              <input type="number" min="0" step="1" value={limitTokens} onChange={(e) => setLimitTokens(e.target.value)} placeholder="e.g. 5000000 (empty = no token limit)" className={inputClass} />
            </label>
          </div>
          <label className="block space-y-1">
            <span className="text-xs font-semibold text-gray-700">Send an alert at these percentages</span>
            <input type="text" value={levels} onChange={(e) => setLevels(e.target.value)} placeholder="50, 80, 100" className={inputClass} />
          </label>
          <label className="flex items-start gap-2.5 p-3 rounded-lg border border-gray-200 cursor-pointer">
            <input type="checkbox" checked={hardStop} onChange={(e) => setHardStop(e.target.checked)} className="mt-0.5 rounded text-[#088ADA]" />
            <span>
              <span className="block text-xs font-semibold text-gray-800">Stop answering when the limit is reached</span>
              <span className="block text-[11px] text-gray-500">The assistant tells people the monthly budget is used up and waits for the next month or a higher limit.</span>
            </span>
          </label>
          <div className="flex justify-end">
            <button type="submit" disabled={busy} className={btn.primary}>
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
              <span>Save budget</span>
            </button>
          </div>
        </form>
      ) : (
        <p className="text-xs text-gray-500">Your JTS administrator sets the budget. Ask them if you need a different limit.</p>
      )}
    </Section>
  );
}
