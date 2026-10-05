"use client";

import { useMemo, useState } from "react";
import { GitBranch, Loader2, RotateCcw, Save, Search, ShieldCheck, SquareKanban } from "lucide-react";
import { updateUserPermissions } from "@/lib/api";
import { DashboardUser } from "@/lib/types";
import { PERMISSION_LIST, ToolPermissions, permissionDefaults, samePermissions } from "@/lib/permissions";
import { Alert, btn, inputClass } from "@/components/ui";

const ROLE_LABEL: Record<string, string> = { jts_admin: "JTS Admin", client_admin: "Client Admin", client_standard: "Team Member" };
const GITHUB = PERMISSION_LIST.filter((p) => p.group === "GitHub");
const JIRA = PERMISSION_LIST.filter((p) => p.group === "Jira");

function effective(u: DashboardUser): ToolPermissions {
  return u.tool_permissions && Object.keys(u.tool_permissions).length ? u.tool_permissions : permissionDefaults(u.role);
}

/** One row per person with a checkbox for each GitHub / Jira permission. Saved per person. */
export function UserPermissionsTab({
  users,
  onSaved,
}: {
  users: DashboardUser[];
  onSaved: (userId: number, permissions: ToolPermissions) => void;
}) {
  const [drafts, setDrafts] = useState<Record<number, ToolPermissions>>({});
  const [busyId, setBusyId] = useState<number | null>(null);
  const [query, setQuery] = useState("");
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return users.filter((u) => !q || [u.name, u.username, u.email, u.client_folder_name].some((v) => (v || "").toLowerCase().includes(q)));
  }, [users, query]);

  const current = (u: DashboardUser) => drafts[u.id] ?? effective(u);
  const dirty = (u: DashboardUser) => Boolean(drafts[u.id]) && !samePermissions(drafts[u.id], effective(u));

  function toggle(u: DashboardUser, key: string, value: boolean) {
    setDrafts((d) => ({ ...d, [u.id]: { ...current(u), [key]: value } }));
  }

  async function save(u: DashboardUser, perms: ToolPermissions | null) {
    setBusyId(u.id);
    setFeedback(null);
    try {
      const res = await updateUserPermissions(u.id, perms);
      setFeedback({ type: "success", message: res.message });
      setDrafts((d) => {
        const next = { ...d };
        delete next[u.id];
        return next;
      });
      onSaved(u.id, res.tool_permissions);
    } catch (e: any) {
      setFeedback({ type: "error", message: e?.message || "We couldn't save the permissions." });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="space-y-4">
      <div className="p-4 rounded-xl bg-white border border-gray-200 shadow-sm space-y-1">
        <h2 className="text-sm font-semibold text-gray-800 flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-[#088ADA]" /> Tool permissions
        </h2>
        <p className="text-xs text-gray-600">
          Choose what each person may ask the assistant to do in Slack. The assistant recognises people by their Slack email, so it must match the
          email shown here. A person who can&apos;t be matched can read but not make changes. Approving a request is separate and isn&apos;t affected.
        </p>
        <p className="text-[11px] text-gray-500">
          New users start with their role&apos;s defaults: Client Admin has everything, Team Member can only read. JTS Admins always have everything.
        </p>
      </div>

      {feedback && (
        <Alert type={feedback.type} onClose={() => setFeedback(null)}>
          {feedback.message}
        </Alert>
      )}

      <div className="relative max-w-sm">
        <Search className="h-3.5 w-3.5 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
        <input
          className={`${inputClass} pl-9 text-xs`}
          placeholder="Search name, username, email or client"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <div className="overflow-x-auto rounded-xl border border-gray-200 shadow-sm bg-white">
        <table className="w-full text-left border-collapse min-w-[900px]">
          <thead className="text-white text-xs uppercase tracking-wider">
            <tr className="bg-[#088ADA]">
              <th rowSpan={2} className="p-3 font-semibold align-bottom">Person</th>
              <th rowSpan={2} className="p-3 font-semibold align-bottom">Role</th>
              <th colSpan={GITHUB.length} className="p-2 font-semibold text-center border-l border-white/30">
                <span className="inline-flex items-center gap-1.5"><GitBranch className="h-3.5 w-3.5" /> GitHub</span>
              </th>
              <th colSpan={JIRA.length} className="p-2 font-semibold text-center border-l border-white/30">
                <span className="inline-flex items-center gap-1.5"><SquareKanban className="h-3.5 w-3.5" /> Jira</span>
              </th>
              <th rowSpan={2} className="p-3 font-semibold text-right align-bottom border-l border-white/30">Actions</th>
            </tr>
            <tr className="bg-[#0778bd]">
              {[...GITHUB, ...JIRA].map((p, i) => (
                <th key={p.key} title={p.hint} className={`p-2 font-medium text-center normal-case tracking-normal text-[11px] ${i === 0 || i === GITHUB.length ? "border-l border-white/30" : ""}`}>
                  {p.short}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200">
            {rows.length === 0 ? (
              <tr>
                <td colSpan={3 + PERMISSION_LIST.length} className="p-6 text-center text-xs text-gray-500">
                  No users match.
                </td>
              </tr>
            ) : (
              rows.map((u, idx) => {
                const isAdmin = u.role === "jts_admin";
                const perms = isAdmin ? permissionDefaults("jts_admin") : current(u);
                const changed = dirty(u);
                const busy = busyId === u.id;
                return (
                  <tr key={u.id} className={`${idx % 2 === 0 ? "bg-white" : "bg-[#f7f7f7]"} ${changed ? "outline outline-1 -outline-offset-1 outline-amber-300" : ""}`}>
                    <td className="p-3">
                      <div className="text-sm font-semibold text-gray-800">{u.name || u.username}</div>
                      <div className="text-[11px] text-gray-500">
                        @{u.username}
                        {u.email ? ` · ${u.email}` : ""}
                      </div>
                      {u.client_folder_name && <div className="text-[11px] text-gray-500">{u.client_folder_name}</div>}
                    </td>
                    <td className="p-3">
                      <span className="text-[11px] font-semibold px-2 py-0.5 rounded-full bg-gray-100 text-gray-700 border border-gray-200">
                        {ROLE_LABEL[u.role] || u.role}
                      </span>
                    </td>
                    {[...GITHUB, ...JIRA].map((p, i) => (
                      <td key={p.key} className={`p-2 text-center ${i === 0 || i === GITHUB.length ? "border-l border-gray-200" : ""}`}>
                        <input
                          type="checkbox"
                          aria-label={`${p.label} for ${u.name || u.username}`}
                          disabled={isAdmin || busy}
                          checked={Boolean(perms[p.key])}
                          onChange={(e) => toggle(u, p.key, e.target.checked)}
                          className="h-4 w-4 rounded border-gray-300 text-[#088ADA] focus:ring-[#088ADA] disabled:opacity-60"
                        />
                      </td>
                    ))}
                    <td className="p-3 text-right border-l border-gray-200">
                      {isAdmin ? (
                        <span className="text-[11px] text-gray-500">Always everything</span>
                      ) : (
                        <div className="inline-flex items-center gap-1.5">
                          <button
                            type="button"
                            onClick={() => save(u, current(u))}
                            disabled={!changed || busy}
                            className={btn.primary}
                          >
                            {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />} Save
                          </button>
                          <button
                            type="button"
                            title="Back to the defaults for this role"
                            onClick={() => save(u, null)}
                            disabled={busy}
                            className={btn.secondary}
                            aria-label={`Reset ${u.name || u.username} to role defaults`}
                          >
                            <RotateCcw className="h-3.5 w-3.5" />
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>
      <p className="text-[11px] text-gray-500">Tick or untick, then click Save on that person&apos;s row. The arrow resets the row to its role defaults.</p>
    </div>
  );
}
