export type ToolPermissions = Record<string, boolean>;

export interface PermissionDef {
  key: string;
  group: "GitHub" | "Jira";
  /** short column title */
  short: string;
  label: string;
  hint: string;
}

/** The nine GitHub / Jira permissions (same keys as the server). */
export const PERMISSION_LIST: PermissionDef[] = [
  { key: "github_read", group: "GitHub", short: "Read", label: "Can read repositories", hint: "Read issues, files, code, commits and pull requests" },
  { key: "github_issues", group: "GitHub", short: "Issues", label: "Can create and comment on issues", hint: "Create issues, update issues and add comments" },
  { key: "github_code", group: "GitHub", short: "Code & files", label: "Can commit code and files", hint: "Create or update files, push files and create branches" },
  { key: "github_prs", group: "GitHub", short: "Pull requests", label: "Can open pull requests", hint: "Open pull requests" },
  { key: "github_sites", group: "GitHub", short: "Websites", label: "Can publish and edit websites", hint: "Publish a built website and propose changes to a published site" },
  { key: "jira_read", group: "Jira", short: "Read", label: "Can read tickets", hint: "Search tickets and read their details" },
  { key: "jira_create", group: "Jira", short: "Create", label: "Can create tickets", hint: "Create new Jira tickets" },
  { key: "jira_edit", group: "Jira", short: "Edit & comment", label: "Can edit and comment", hint: "Change a ticket's title, description, priority, labels and add comments" },
  { key: "jira_close", group: "Jira", short: "Move / close", label: "Can move or close tickets", hint: "Change a ticket's status, for example to Done" },
];

/** What a role gets until someone changes the checkboxes (matches the server). */
export function permissionDefaults(role: string): ToolPermissions {
  const everything = role === "jts_admin" || role === "client_admin";
  return Object.fromEntries(PERMISSION_LIST.map((p) => [p.key, everything || p.key.endsWith("_read")]));
}

export function samePermissions(a: ToolPermissions, b: ToolPermissions): boolean {
  return PERMISSION_LIST.every((p) => Boolean(a[p.key]) === Boolean(b[p.key]));
}
