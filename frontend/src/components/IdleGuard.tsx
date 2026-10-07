"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Clock } from "lucide-react";
import { sendHeartbeat } from "@/lib/api";
import { btn } from "@/components/ui";

const WARN_SECONDS = 60; // warn this long before signing out
const BEAT_EVERY_MS = 60_000; // tell the server "still here" at most once a minute, and only while the person is active
const CHECK_EVERY_MS = 5_000;

/**
 * Signs the person out after a period of inactivity (15 minutes by default, set by the server).
 * Only real use counts (mouse, keys, touch, scroll). Pages refreshing themselves in the background do not.
 * One minute before, a notice offers "Stay signed in".
 */
export function IdleGuard() {
  const [idleMs, setIdleMs] = useState(15 * 60_000);
  const [warning, setWarning] = useState<number | null>(null); // seconds left, or null
  const lastActivity = useRef<number>(Date.now());
  const lastBeat = useRef<number>(0);
  const warningShown = useRef(false);
  const idleMsRef = useRef(idleMs);
  idleMsRef.current = idleMs;

  const signOut = useCallback(() => {
    try {
      sessionStorage.clear();
      sessionStorage.setItem("jts_access_message", "You were signed out after a period of inactivity. Please sign in again.");
    } catch {}
    window.location.href = "/login";
  }, []);

  const beat = useCallback(async () => {
    lastBeat.current = Date.now();
    try {
      const res = await sendHeartbeat();
      if (res?.idle_minutes) setIdleMs(res.idle_minutes * 60_000);
    } catch {
      // A refused heartbeat means the server already ended the session; the next request sends the person to the login page.
    }
  }, []);

  const stay = useCallback(() => {
    lastActivity.current = Date.now();
    warningShown.current = false;
    setWarning(null);
    beat();
  }, [beat]);

  useEffect(() => {
    const onActivity = () => {
      if (warningShown.current) return; // once the notice is up, only its button counts
      lastActivity.current = Date.now();
    };
    const events = ["mousemove", "mousedown", "keydown", "scroll", "touchstart", "wheel", "click"];
    events.forEach((e) => window.addEventListener(e, onActivity, { passive: true }));
    beat();

    const timer = window.setInterval(() => {
      const now = Date.now();
      const idle = now - lastActivity.current;
      const limit = idleMsRef.current;
      if (idle >= limit) {
        signOut();
        return;
      }
      if (idle >= limit - WARN_SECONDS * 1000) {
        warningShown.current = true;
        setWarning(Math.max(0, Math.ceil((limit - idle) / 1000)));
        return;
      }
      if (idle < BEAT_EVERY_MS && now - lastBeat.current >= BEAT_EVERY_MS) beat();
    }, CHECK_EVERY_MS);

    return () => {
      events.forEach((e) => window.removeEventListener(e, onActivity));
      window.clearInterval(timer);
    };
  }, [beat, signOut]);

  // Count the last seconds down smoothly while the notice is up
  useEffect(() => {
    if (warning === null) return;
    const t = window.setInterval(() => {
      const left = Math.ceil((idleMsRef.current - (Date.now() - lastActivity.current)) / 1000);
      if (left <= 0) signOut();
      else setWarning(left);
    }, 1000);
    return () => window.clearInterval(t);
  }, [warning === null, signOut]); // eslint-disable-line react-hooks/exhaustive-deps

  if (warning === null) return null;
  return (
    <div className="fixed inset-0 z-[100] bg-black/50 backdrop-blur-sm flex items-center justify-center p-4" role="alertdialog" aria-modal="true" aria-label="Signing out soon">
      <div className="bg-white rounded-2xl max-w-sm w-full p-6 shadow-2xl border border-gray-200 text-center space-y-4">
        <div className="mx-auto h-12 w-12 rounded-full bg-amber-50 border border-amber-200 flex items-center justify-center">
          <Clock className="h-6 w-6 text-amber-600" />
        </div>
        <div>
          <h2 className="text-base font-semibold text-gray-900">Still there?</h2>
          <p className="text-sm text-gray-600 mt-1">
            For your security you&apos;ll be signed out in <span className="font-semibold tabular-nums">{warning}</span> second{warning === 1 ? "" : "s"} because there has been no activity.
          </p>
        </div>
        <div className="flex gap-2 justify-center">
          <button onClick={stay} className={btn.primary} autoFocus>
            Stay signed in
          </button>
          <button onClick={signOut} className={btn.secondary}>
            Sign out now
          </button>
        </div>
      </div>
    </div>
  );
}
