"use client";

import { useCallback, useEffect, useState } from "react";
import { Building2, Check, Copy, ExternalLink, Loader2, MessagesSquare, Plus, ShieldCheck } from "lucide-react";
import { SlackWorkspaceLink, fetchWorkspaceClients, setWorkspaceClient } from "@/lib/api";
import { ChannelFolder } from "@/lib/types";
import { Alert, Badge, LoadingState, Section, btn, inputClass, tbl } from "@/components/ui";

/** JTS Admin: which client does each connected Slack workspace belong to? */
export function WorkspaceClientsCard({ folders }: { folders: ChannelFolder[] }) {
  const [rows, setRows] = useState<SlackWorkspaceLink[] | null>(null);
  const [deleteLink, setDeleteLink] = useState("");
  const [installLink, setInstallLink] = useState("");
  const [copied, setCopied] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetchWorkspaceClients();
      setRows(res.workspaces);
      setDeleteLink(res.deleteLink);
      setInstallLink(res.installLink);
    } catch (e: any) {
      setFeedback({ type: "error", message: e?.message || "We couldn't load the Slack workspaces." });
      setRows([]);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function change(teamId: string, value: string) {
    setBusyId(teamId);
    setFeedback(null);
    try {
      const res = await setWorkspaceClient(teamId, value ? Number(value) : null);
      setFeedback({ type: "success", message: res.message });
      await load();
    } catch (e: any) {
      setFeedback({ type: "error", message: e?.message || "We couldn't save that." });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <Section
      icon={MessagesSquare}
      title={`Slack workspaces (${rows?.length ?? 0})`}
      description="If a whole workspace belongs to one client, link it here. Personal chats with the bot, and any channel that isn't in another client's folder, are then billed to and handled as that client. Leave a shared workspace unlinked."
      actions={
        installLink ? (
          <>
            <button
              type="button"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(installLink);
                  setCopied(true);
                  setTimeout(() => setCopied(false), 2000);
                } catch {}
              }}
              className={btn.secondary}
              title="Copy the install link to send to the Slack Owner or Admin of the client's workspace"
            >
              {copied ? <Check className="h-3.5 w-3.5 text-emerald-600" /> : <Copy className="h-3.5 w-3.5" />}
              {copied ? "Copied" : "Copy install link"}
            </button>
            <a href={installLink} target="_blank" rel="noopener noreferrer" className={btn.primary} title="Opens Slack: choose the workspace in the top-right and click Allow">
              <Plus className="h-3.5 w-3.5" />
              Add Slack workspace
            </a>
          </>
        ) : undefined
      }
      bodyClassName="p-5 space-y-4"
    >
      {feedback && (
        <Alert type={feedback.type} onClose={() => setFeedback(null)}>
          {feedback.message}
        </Alert>
      )}

      {rows === null ? (
        <LoadingState label="Loading Slack workspaces..." />
      ) : rows.length === 0 ? (
        <p className="text-sm text-gray-600">No Slack workspaces are connected yet.</p>
      ) : (
        <div className={tbl.wrap}>
          <table className={tbl.table}>
            <thead className={tbl.head}>
              <tr>
                <th className={tbl.th}>Slack workspace</th>
                <th className={tbl.th}>Workspace ID</th>
                <th className={`${tbl.th} text-center`}>Chats seen</th>
                <th className={tbl.th}>Belongs to client</th>
                <th className={tbl.th}>Delete key messages</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((w) => (
                <tr key={w.team_id} className={tbl.row}>
                  <td className={`${tbl.td} font-semibold text-gray-900`}>{w.team_name}</td>
                  <td className={tbl.td}>
                    <span className="font-mono text-[11px] text-sky-700 bg-sky-50 border border-sky-200 px-2 py-0.5 rounded-md">{w.team_id}</span>
                  </td>
                  <td className={`${tbl.td} text-center tabular-nums`}>{Number(w.chat_count) || 0}</td>
                  <td className={tbl.td}>
                    <div className="flex items-center gap-2 max-w-xs">
                      <Building2 className="h-3.5 w-3.5 text-gray-400 shrink-0" />
                      <select
                        className={`${inputClass} text-xs`}
                        value={w.folder_id ?? ""}
                        disabled={busyId === w.team_id}
                        onChange={(e) => change(w.team_id, e.target.value)}
                        aria-label={`Client for ${w.team_name}`}
                      >
                        <option value="">Shared (no single client)</option>
                        {folders.map((f) => (
                          <option key={f.id} value={f.id}>
                            {f.name}
                          </option>
                        ))}
                      </select>
                      {busyId === w.team_id && <Loader2 className="h-3.5 w-3.5 animate-spin text-gray-400" />}
                    </div>
                  </td>
                  <td className={tbl.td}>
                    {w.can_delete_messages ? (
                      <Badge tone="green">
                        <ShieldCheck className="h-3 w-3" /> On
                      </Badge>
                    ) : (
                      <div className="flex items-center gap-2 flex-wrap">
                        <Badge tone="gray">Off</Badge>
                        {deleteLink && (
                          <a
                            href={deleteLink}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1 text-xs font-semibold text-[#088ADA] hover:underline"
                            title="An admin or owner of this Slack workspace must open this link while signed in to it and click Allow"
                          >
                            Turn on <ExternalLink className="h-3 w-3" />
                          </a>
                        )}
                      </div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-[11px] text-gray-500">
        To connect a new workspace, a Slack <strong>Owner or Admin</strong> of that workspace signs in to it in the browser and opens the install link
        (<strong>Add Slack workspace</strong>, or send them <strong>Copy install link</strong>), picks the workspace in the top-right of the Slack page and clicks <strong>Allow</strong>. Then invite the bot to a channel with <span className="font-mono">/invite @jpt</span>.
      </p>
      <p className="text-[11px] text-gray-500">
        &ldquo;Turn on&rdquo; must be opened by an Owner or Admin of that Slack workspace, signed in to it. It lets the bot delete a message that contains a key.
      </p>
    </Section>
  );
}
