"use client";

import { useCallback, useEffect, useState } from "react";
import { Building2, Loader2, MessagesSquare, ShieldCheck } from "lucide-react";
import { SlackWorkspaceLink, fetchWorkspaceClients, setWorkspaceClient } from "@/lib/api";
import { ChannelFolder } from "@/lib/types";
import { Alert, LoadingState, inputClass } from "@/components/ui";

/** JTS Admin: which client does each connected Slack workspace belong to? */
export function WorkspaceClientsCard({ folders }: { folders: ChannelFolder[] }) {
  const [rows, setRows] = useState<SlackWorkspaceLink[] | null>(null);
  const [deleteLink, setDeleteLink] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ type: "success" | "error"; message: string } | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetchWorkspaceClients();
      setRows(res.workspaces);
      setDeleteLink(res.deleteLink);
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
    <div className="pt-4 border-t border-gray-200 space-y-3">
      <div>
        <h2 className="text-sm font-semibold text-gray-800 flex items-center gap-2">
          <MessagesSquare className="h-4 w-4 text-[#088ADA]" /> Slack workspaces ({rows?.length ?? 0})
        </h2>
        <p className="text-xs text-gray-500 mt-0.5">
          If a whole workspace belongs to one client, link it here. Personal chats with the bot, and any channel that isn&apos;t in another
          client&apos;s folder, are then billed to and handled as that client. Leave a shared workspace unlinked.
        </p>
      </div>

      {feedback && (
        <Alert type={feedback.type} onClose={() => setFeedback(null)}>
          {feedback.message}
        </Alert>
      )}

      {rows === null ? (
        <LoadingState label="Loading Slack workspaces..." />
      ) : rows.length === 0 ? (
        <p className="text-xs text-gray-600">No Slack workspaces are connected yet.</p>
      ) : (
        <div className="overflow-x-auto rounded-xl border border-gray-200 shadow-sm bg-white">
          <table className="w-full text-left border-collapse">
            <thead className="bg-[#088ADA] text-white text-xs uppercase tracking-wider">
              <tr>
                <th className="p-3 font-semibold">Slack workspace</th>
                <th className="p-3 font-semibold">Workspace ID</th>
                <th className="p-3 font-semibold text-center">Chats seen</th>
                <th className="p-3 font-semibold">Belongs to client</th>
                <th className="p-3 font-semibold">Delete key messages</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-200">
              {rows.map((w, idx) => (
                <tr key={w.team_id} className={idx % 2 === 0 ? "bg-white" : "bg-[#f7f7f7]"}>
                  <td className="p-3 text-sm font-semibold text-gray-800">{w.team_name}</td>
                  <td className="p-3">
                    <span className="font-mono text-[11px] text-sky-700 bg-sky-50 border border-sky-200 px-2 py-0.5 rounded-md">{w.team_id}</span>
                  </td>
                  <td className="p-3 text-center text-xs text-gray-600">{Number(w.chat_count) || 0}</td>
                  <td className="p-3">
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
                  <td className="p-3">
                    {w.can_delete_messages ? (
                      <span className="inline-flex items-center gap-1 text-[11px] font-semibold px-2 py-0.5 rounded-full border bg-emerald-50 text-emerald-700 border-emerald-200">
                        <ShieldCheck className="h-3 w-3" /> On
                      </span>
                    ) : (
                      <div className="space-y-1">
                        <span className="inline-flex text-[11px] font-semibold px-2 py-0.5 rounded-full border bg-gray-100 text-gray-600 border-gray-200">Off</span>
                        {deleteLink && (
                          <div>
                            <a
                              href={deleteLink}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-[11px] font-semibold text-[#088ADA] hover:underline"
                              title="An admin or owner of this Slack workspace must open this link while signed in to it and click Allow"
                            >
                              Turn on (a workspace admin clicks this)
                            </a>
                          </div>
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
    </div>
  );
}
