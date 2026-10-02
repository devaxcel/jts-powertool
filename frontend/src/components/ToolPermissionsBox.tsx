"use client";

import { GitBranch, SquareKanban, ShieldCheck } from "lucide-react";

export type ToolPermissions = Record<string, boolean>;

export const PERMISSION_LIST: Array<{ key: string; group: "GitHub" | "Jira"; label: string; hint: string }> = [
  { key: "github_read", group: "GitHub", label: "Can read repositories", hint: "Read issues, files, code, commits and pull requests" },
  { key: "github_write", group: "GitHub", label: "Can make changes", hint: "Issues, files, pull requests, publish and edit websites" },
  { key: "jira_read", group: "Jira", label: "Can read tickets", hint: "Search tickets and read their details" },
  { key: "jira_create", group: "Jira", label: "Can create tickets", hint: "Create new Jira tickets" },
  { key: "jira_edit", group: "Jira", label: "Can edit and comment", hint: "Change title, description, priority, labels; add comments" },
  { key: "jira_close", group: "Jira", label: "Can move or close tickets", hint: "Change a ticket's status, for example to Done" },
];

/** What a role gets until someone changes the checkboxes (matches the server). */
export function permissionDefaults(role: string): ToolPermissions {
  const everything = role === "jts_admin" || role === "client_admin";
  return Object.fromEntries(PERMISSION_LIST.map((p) => [p.key, everything || p.key.endsWith("_read")]));
}

export function ToolPermissionsBox({
  value,
  onChange,
  role,
}: {
  value: ToolPermissions;
  onChange: (next: ToolPermissions) => void;
  role: string;
}) {
  const isAdmin = role === "jts_admin";
  const shown = isAdmin ? permissionDefaults("jts_admin") : value;

  return (
    <div className="pt-2 border-t border-gray-100 space-y-2.5">
      <div>
        <label className="block text-gray-700 font-bold">Tool permissions</label>
        <p className="text-[11px] text-gray-500">
          What this person may ask the assistant to do in Slack. The bot finds them by their Slack email, so it must match the email here.
        </p>
      </div>

      {isAdmin && (
        <div className="p-2 rounded-lg bg-sky-50 border border-sky-200 text-[11px] text-sky-800 flex items-center gap-1.5">
          <ShieldCheck className="h-3.5 w-3.5 shrink-0" /> JTS Admins always have every permission.
        </div>
      )}

      {(["GitHub", "Jira"] as const).map((group) => (
        <div key={group} className="p-3 bg-gray-50/80 border border-gray-200 rounded-xl space-y-2">
          <div className="flex items-center gap-1.5 border-b border-gray-200/60 pb-1.5 font-semibold text-gray-800 text-[11px]">
            {group === "GitHub" ? <GitBranch className="h-3.5 w-3.5 text-[#088ADA]" /> : <SquareKanban className="h-3.5 w-3.5 text-[#0052CC]" />}
            {group} permissions
          </div>
          <div className="space-y-1.5 pt-0.5">
            {PERMISSION_LIST.filter((p) => p.group === group).map((p) => (
              <label key={p.key} className={`flex items-start gap-2 text-gray-700 select-none ${isAdmin ? "opacity-70" : "cursor-pointer"}`}>
                <input
                  type="checkbox"
                  disabled={isAdmin}
                  checked={Boolean(shown[p.key])}
                  onChange={(e) => onChange({ ...value, [p.key]: e.target.checked })}
                  className="mt-0.5 rounded border-gray-300 text-[#088ADA] focus:ring-[#088ADA] h-3.5 w-3.5"
                />
                <span>
                  {p.label}
                  <span className="block text-[10px] text-gray-500">{p.hint}</span>
                </span>
              </label>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
