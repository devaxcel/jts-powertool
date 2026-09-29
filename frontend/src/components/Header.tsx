"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Menu, UserCircle2, LogOut } from "lucide-react";
import { useMobileNav } from "@/components/DashboardShell";
import { logout } from "@/lib/api";

const TITLES: Array<[string, string]> = [
  ["/approvals", "Approvals"],
  ["/billing", "Usage & Billing"],
  ["/users", "Users"],
  ["/organizations", "Organizations"],
  ["/my-tasks", "My Activity"],
  ["/keys", "API Keys"],
  ["/folders", "Clients & Channels"],
  ["/logs", "Activity Log"],
  ["/context", "Conversation Inspector"],
  ["/database", "Database"],
  ["/global-settings", "System Settings"],
  ["/settings", "My Profile"],
];

const ROLE_LABELS: Record<string, string> = {
  jts_admin: "JTS Admin",
  admin: "JTS Admin",
  client_admin: "Client Admin",
  client_standard: "Team Member",
};

export function Header() {
  const pathname = usePathname();
  const { toggleMobile } = useMobileNav();
  const [displayName, setDisplayName] = useState("");
  const [role, setRole] = useState("");

  useEffect(() => {
    try {
      const u = JSON.parse(sessionStorage.getItem("jts_user") || "{}");
      setDisplayName(u.name || u.username || "");
      setRole(sessionStorage.getItem("jts_simulated_role") || u.role || "");
    } catch {}
  }, [pathname]);

  const title =
    pathname === "/" ? "Dashboard" : TITLES.find(([prefix]) => pathname.startsWith(prefix))?.[1] || "Dashboard";

  return (
    <header className="h-14 border-b border-gray-200 bg-white px-4 sm:px-6 flex items-center justify-between sticky top-0 z-10">
      <div className="flex items-center gap-3 min-w-0">
        <button
          type="button"
          onClick={toggleMobile}
          className="p-1.5 -ml-1 rounded-lg text-gray-600 hover:text-gray-900 hover:bg-gray-100 lg:hidden transition shrink-0"
          aria-label="Open navigation menu"
        >
          <Menu className="h-5 w-5" />
        </button>
        <span className="text-sm font-semibold text-gray-800 truncate">{title}</span>
      </div>

      <div className="flex items-center gap-1.5">
        <Link
          href="/settings"
          className={`flex items-center gap-2 pl-1.5 pr-3 py-1 rounded-xl transition ${
            pathname === "/settings" ? "bg-[#088ADA]/10" : "hover:bg-gray-100"
          }`}
          title="My profile"
        >
          <UserCircle2 className="h-7 w-7 text-[#088ADA]" />
          <div className="hidden sm:block text-left leading-tight">
            <div className="text-xs font-semibold text-gray-800 max-w-[160px] truncate">{displayName || "My profile"}</div>
            {role && <div className="text-[11px] text-gray-500">{ROLE_LABELS[role] || role}</div>}
          </div>
        </Link>
        <button
          type="button"
          onClick={() => logout()}
          className="p-2 rounded-lg text-gray-500 hover:text-rose-600 hover:bg-rose-50 transition"
          title="Sign out"
          aria-label="Sign out"
        >
          <LogOut className="h-4 w-4" />
        </button>
      </div>
    </header>
  );
}
