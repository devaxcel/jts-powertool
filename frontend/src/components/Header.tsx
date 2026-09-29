"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Sparkles, Radio, Menu, User } from "lucide-react";
import { useMobileNav } from "@/components/DashboardShell";

export function Header() {
  const pathname = usePathname();
  const { toggleMobile } = useMobileNav();

  const titles: Record<string, string> = {
    "/": "System Overview",
    "/approvals": "Human-in-the-Loop Approvals",
    "/billing": "Token & Billing Control Center",
    "/users": "Multi-Tenant User Management",
    "/organizations": "Organization Management",
    "/my-tasks": "My Personal Tasks & Activity",
    "/keys": "API Keys & Secrets Vault",
    "/folders": "Channel Folders & Slack Projects",
    "/logs": "Live Telemetry & Event Stream",
    "/context": "Claude Context & Turn Inspector",
    "/database": "PostgreSQL Database Explorer",
    "/settings": "Account Settings & Profile",
    "/global-settings": "Global Settings & Timezone",
  };

  const title = titles[pathname] || "Dashboard";

  return (
    <>
      <header className="h-16 border-b border-gray-200 bg-white px-6 flex items-center justify-between sticky top-0 z-10 shadow-sm">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={toggleMobile}
            className="p-1.5 -ml-2 rounded-lg text-gray-600 hover:text-gray-900 hover:bg-gray-100 lg:hidden transition shrink-0"
            aria-label="Toggle navigation menu"
          >
            <Menu className="h-5 w-5" />
          </button>
          <h2 className="text-base font-semibold text-gray-800">{title}</h2>
          <span className="text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-600 font-mono border border-gray-200">
            Production
          </span>
        </div>

        <div className="flex items-center gap-3 text-xs">
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-emerald-50 border border-emerald-200 text-emerald-600 font-medium">
            <Radio className="h-3.5 w-3.5 animate-pulse text-emerald-500" />
            <span>Worker Listening</span>
          </div>

          <div className="flex items-center gap-2 text-gray-600 bg-gray-100 px-3 py-1.5 rounded-xl border border-gray-200">
            <Sparkles className="h-3.5 w-3.5 text-[#088ADA]" />
            <span>Model: Claude 3.5 / 4.5</span>
          </div>

          <Link
            href="/settings"
            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-xl border transition font-medium ${
              pathname === "/settings"
                ? "bg-[#088ADA] text-white border-[#088ADA] shadow-sm"
                : "bg-gray-100 hover:bg-gray-200 text-gray-700 border-gray-200"
            }`}
            title="Profile Settings"
          >
            <User className={`h-3.5 w-3.5 ${pathname === "/settings" ? "text-white" : "text-[#088ADA]"}`} />
            <span>Profile Settings</span>
          </Link>
        </div>
      </header>
    </>
  );
}
