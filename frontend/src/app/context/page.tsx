"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Cpu,
  User,
  Bot,
  RefreshCw,
  Sliders,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { fetchContextHistory, fetchContextSnapshotById } from "@/lib/api";
import { ContextSnapshot, formatLocalDateTime } from "@/lib/types";

export default function ContextPage() {
  const [snapshots, setSnapshots] = useState<ContextSnapshot[]>([]);
  const [selectedSnapshot, setSelectedSnapshot] = useState<ContextSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [showSystemPrompt, setShowSystemPrompt] = useState(false);

  const selectSnapshot = useCallback(async (snapshot: ContextSnapshot) => {
    setSelectedSnapshot(snapshot);
    if (!snapshot.system_prompt || !snapshot.messages_sent) {
      try {
        const full = await fetchContextSnapshotById(snapshot.id);
        if (full) {
          setSelectedSnapshot(full);
        }
      } catch (e) {
        console.error("Error loading full snapshot details:", e);
      }
    }
  }, []);

  const loadContext = useCallback(async () => {
    setLoading(true);
    try {
      const history = await fetchContextHistory(30);
      setSnapshots(history);
      if (history.length > 0) {
        selectSnapshot(history[0]);
      }
    } catch (e) {
      console.error("Error loading context:", e);
    } finally {
      setLoading(false);
    }
  }, [selectSnapshot]);

  useEffect(() => {
    loadContext();
  }, [loadContext]);

  return (
    <div className="space-y-6 max-w-7xl mx-auto flex flex-col min-h-[calc(100vh-8rem)]">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 pb-4 border-b border-gray-200">
        <div className="flex items-center gap-2.5">
          <div className="h-9 w-9 rounded-xl bg-gray-100 border border-gray-200 flex items-center justify-center text-[#088ADA]">
            <Cpu className="h-5 w-5" />
          </div>
          <div>
            <h1 className="text-xl font-bold text-gray-800 flex items-center gap-2">
              <span>Claude Context &amp; Turn Inspector</span>
            </h1>
            <p className="text-xs text-gray-500 mt-0.5">
              Audit conversation histories, token payloads, vector memories, and exact prompts sent to Claude.
            </p>
          </div>
        </div>

        <button
          onClick={loadContext}
          className="flex items-center gap-1.5 px-3 py-2 rounded-xl bg-gray-100 hover:bg-gray-200 text-gray-700 text-xs font-medium border border-gray-200 transition shadow-sm"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin text-[#088ADA]" : ""}`} />
          <span>Refresh</span>
        </button>
      </div>

      {/* Main Two-Panel Layout */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 flex-1 min-h-0">
        {/* Left Column: Snapshots History List */}
        <div className="bg-white border border-gray-200 rounded-2xl flex flex-col overflow-hidden shadow-sm">
          <div className="p-3 border-b border-gray-200 bg-gray-50 text-xs font-semibold text-gray-600 flex items-center justify-between">
            <span>Recorded Sessions</span>
            <span className="text-[11px] text-gray-400 font-mono">{snapshots.length} total</span>
          </div>

          <div className="flex-1 overflow-y-auto divide-y divide-gray-100 p-2 space-y-1">
            {snapshots.length === 0 ? (
              <div className="p-8 text-center text-xs text-gray-400">
                No context snapshots recorded yet.
              </div>
            ) : (
              snapshots.map((s) => {
                const isSelected = selectedSnapshot?.id === s.id;
                const turnCount = s.messages_sent?.length ?? s.files_included?.length ?? 0;

                return (
                  <button
                    key={s.id}
                    onClick={() => selectSnapshot(s)}
                    className={`w-full text-left p-3 rounded-xl transition text-xs space-y-1.5 ${
                      isSelected
                        ? "bg-gray-100 border border-gray-300 text-gray-800 shadow-sm font-semibold"
                        : "hover:bg-gray-50 text-gray-600"
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-semibold text-gray-700 truncate flex items-center gap-1.5">
                        <User className="h-3 w-3 text-[#088ADA] shrink-0" />
                        {s.user_name || "User"}
                      </span>
                      <span className="text-[10px] text-gray-400 font-mono">
                        {formatLocalDateTime(s.created_at)}
                      </span>
                    </div>

                    <p className="text-gray-600 line-clamp-2 leading-relaxed text-[11px] font-medium">
                      {s.prompt_text || "[File / media prompt]"}
                    </p>

                    <div className="flex items-center gap-2 text-[10px] text-gray-400">
                      <span className="px-1.5 py-0.5 rounded bg-gray-100 font-mono text-gray-600 border border-gray-200">
                        {s.model || "Claude"}
                      </span>
                      <span className="font-mono bg-gray-100 text-gray-700 px-1.5 py-0.5 rounded border border-gray-200">
                        {turnCount} turns
                      </span>
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </div>

        {/* Right Column: Selected Context Turn Details */}
        <div className="md:col-span-2 bg-white border border-gray-200 rounded-2xl flex flex-col overflow-hidden shadow-sm">
          {selectedSnapshot ? (
            <div className="flex-1 flex flex-col min-h-0">
              {/* Snapshot Detail Header */}
              <div className="p-4 border-b border-gray-200 bg-gray-50 space-y-2 shrink-0">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-bold px-2.5 py-0.5 rounded bg-gray-100 text-gray-700 border border-gray-200 font-mono">
                      {selectedSnapshot.model}
                    </span>
                    <span className="text-xs text-gray-500 font-mono">
                      Session: {selectedSnapshot.session_id ? selectedSnapshot.session_id.slice(0, 22) + "..." : "default"}
                    </span>
                  </div>
                  <span className="text-xs text-gray-400 font-mono">
                    {formatLocalDateTime(selectedSnapshot.created_at)}
                  </span>
                </div>

                <div className="flex items-center gap-4 text-xs text-gray-500 flex-wrap">
                  <span>
                    User: <strong className="text-gray-800">{selectedSnapshot.user_name}</strong>
                  </span>
                  <span>
                    Turns Preserved:{" "}
                    <strong className="text-gray-800 font-mono">
                      {selectedSnapshot.messages_sent?.length || 0}
                    </strong>
                  </span>
                  {selectedSnapshot.files_included && selectedSnapshot.files_included.length > 0 && (
                    <span>
                      Files:{" "}
                      <strong className="text-gray-700 font-mono">
                        {selectedSnapshot.files_included.join(", ")}
                      </strong>
                    </span>
                  )}
                </div>
              </div>

              {/* Message & Context Viewer */}
              <div className="flex-1 overflow-y-auto p-4 space-y-4 min-h-0">

                {/* System Prompt & Vector Memories Box */}
                {selectedSnapshot.system_prompt && (
                  <div className="rounded-xl border border-gray-200 bg-gray-50 overflow-hidden text-xs">
                    <button
                      onClick={() => setShowSystemPrompt(!showSystemPrompt)}
                      className="w-full p-3 flex items-center justify-between text-left font-semibold text-gray-800 hover:bg-gray-100 transition"
                    >
                      <div className="flex items-center gap-2">
                        <Sliders className="h-4 w-4 text-[#088ADA]" />
                        <span>Active System Prompt &amp; Vector Memories Passed to Claude</span>
                        {selectedSnapshot.system_prompt.includes("[RELEVANT VECTOR MEMORIES") && (
                          <span className="text-[10px] px-2 py-0.5 rounded-full bg-gray-100 text-gray-700 border border-gray-200 font-semibold">
                            Vector Memories Attached
                          </span>
                        )}
                      </div>
                      {showSystemPrompt ? (
                        <ChevronUp className="h-4 w-4 text-[#088ADA]" />
                      ) : (
                        <ChevronDown className="h-4 w-4 text-[#088ADA]" />
                      )}
                    </button>

                    {showSystemPrompt && (
                      <div className="p-3 border-t border-gray-200 bg-gray-100/60 font-mono text-[11px] text-gray-800 whitespace-pre-wrap leading-relaxed max-h-72 overflow-y-auto">
                        {selectedSnapshot.system_prompt}
                      </div>
                    )}
                  </div>
                )}

                {/* Conversation Turns List */}
                {selectedSnapshot.messages_sent && selectedSnapshot.messages_sent.length > 0 ? (
                  selectedSnapshot.messages_sent.map((turn, idx) => {
                    const isUser = turn.role === "user";
                    return (
                      <div
                        key={idx}
                        className={`p-4 rounded-xl border text-xs space-y-2 transition ${
                          isUser
                            ? "bg-white border-gray-200 text-gray-800 shadow-sm"
                            : "bg-gray-50 border-gray-200 text-gray-800 shadow-sm"
                        }`}
                      >
                        <div className="flex items-center justify-between font-semibold border-b pb-2 border-gray-200">
                          <div className="flex items-center gap-2">
                            {isUser ? (
                              <>
                                <User className="h-4 w-4 text-gray-500" />
                                <span className="text-gray-800 font-bold">{turn.speaker || "User"}</span>
                              </>
                            ) : (
                              <>
                                <Bot className="h-4 w-4 text-[#088ADA]" />
                                <span className="text-gray-800 font-bold">Claude Assistant</span>
                              </>
                            )}
                          </div>
                          <span className="text-[10px] text-gray-500 font-mono bg-gray-100 px-2 py-0.5 rounded border border-gray-200">
                            Turn #{idx + 1}
                          </span>
                        </div>

                        <div className="whitespace-pre-wrap font-mono text-[11px] leading-relaxed text-gray-800 overflow-x-auto max-h-96 pt-1">
                          {turn.content}
                        </div>
                      </div>
                    );
                  })
                ) : (
                  <div className="p-6 text-center text-xs text-gray-400 bg-gray-50 rounded-xl border border-dashed border-gray-200">
                    No historical conversation turn payload was attached to this prompt snapshot.
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="flex-1 flex items-center justify-center text-gray-400 text-xs">
              Select a session from the list to inspect context.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
