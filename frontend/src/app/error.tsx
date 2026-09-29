"use client";

import { useEffect } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error("Client Error Caught:", error);
  }, [error]);

  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] p-6 text-center space-y-4">
      <div className="p-3 bg-rose-50 text-rose-500 rounded-full border border-rose-500/20">
        <AlertTriangle className="h-8 w-8" />
      </div>
      <div className="space-y-2 max-w-md">
        <h2 className="text-lg font-bold text-gray-700">Something went wrong</h2>
        <p className="text-xs text-gray-400 font-mono bg-gray-50 p-3 rounded-lg border border-gray-200 break-all text-left">
          {error?.message || "An unexpected error occurred while rendering this page."}
        </p>
      </div>
      <button
        onClick={() => reset()}
        className="flex items-center gap-2 px-4 py-2 bg-[#088ADA] hover:bg-[#0778bd] text-white text-xs font-semibold rounded-lg transition"
      >
        <RefreshCw className="h-3.5 w-3.5" />
        <span>Try Again</span>
      </button>
    </div>
  );
}
