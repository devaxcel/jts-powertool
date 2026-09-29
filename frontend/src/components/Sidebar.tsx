"use client";

import { useState, useEffect } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  ShieldCheck,
  Activity,
  Cpu,
  Database,
  Terminal,
  KeyRound,
  Folder,
  Receipt,
  User,
  Users,
  ListTodo,
  Globe,
  LogOut,
  X,
  ChevronDown,
  Building2,
  UserPlus,
} from "lucide-react";
import { logout } from "@/lib/api";
import { useMobileNav } from "@/components/DashboardShell";

export function Sidebar() {
  const pathname = usePathname();
  const { isMobileOpen, closeMobile } = useMobileNav();
  const [username, setUsername] = useState("admin");
  const [role, setRole] = useState<string>("jts_admin");
  const [isUserMgmtOpen, setIsUserMgmtOpen] = useState(
    pathname.startsWith("/users") || pathname.startsWith("/organizations")
  );

  useEffect(() => {
    if (typeof window !== "undefined") {
      try {
        const rawUser = sessionStorage.getItem("jts_user");
        const u = JSON.parse(rawUser || "{}");
        if (u.username) setUsername(u.username);

        const savedSim = sessionStorage.getItem("jts_simulated_role");
        const activeRole = savedSim || u.role || "jts_admin";
        setRole(activeRole);
      } catch {}
    }
  }, []);

  // Normalize role string (treat 'admin', 'jts_admin', or empty as master admin)
  const normRole = (!role || role === "admin" || role === "jts_admin") ? "jts_admin" : role;

  const allNavItems = [
    { label: "Overview", href: "/", icon: LayoutDashboard, roles: ["jts_admin", "admin", "client_admin"] },
    { label: "My Tasks & Activity", href: "/my-tasks", icon: ListTodo, roles: ["client_standard"] },
    { label: "HITL Approvals", href: "/approvals", icon: ShieldCheck, badge: "Live", roles: ["jts_admin", "admin", "client_admin", "client_standard"] },
    { label: "Token & Billing", href: "/billing", icon: Receipt, roles: ["jts_admin", "admin", "client_admin"] },
    { label: "Organization & Users", href: "/users", icon: Users, roles: ["jts_admin", "admin"] },
    { label: "API Keys Vault", href: "/keys", icon: KeyRound, roles: ["jts_admin", "admin"] },
    { label: "Channels & Folders", href: "/folders", icon: Folder, roles: ["jts_admin", "admin", "client_admin"] },
    { label: "Live Telemetry", href: "/logs", icon: Activity, roles: ["jts_admin", "admin"] },
    { label: "Claude Context", href: "/context", icon: Cpu, roles: ["jts_admin", "admin"] },
    { label: "Database Explorer", href: "/database", icon: Database, roles: ["jts_admin", "admin"] },
    { label: "Global Settings", href: "/global-settings", icon: Globe, roles: ["jts_admin", "admin"] },
  ];

  let visibleNavItems = allNavItems.filter((item) => item.roles.includes(normRole));
  if (visibleNavItems.length === 0) {
    visibleNavItems = allNavItems.filter((item) => item.roles.includes("jts_admin"));
  }

  const roleLabel = normRole === "jts_admin" ? "JTS Admin" : normRole === "client_admin" ? "Client Admin" : "Client Standard";

  return (
    <>
      {/* Mobile Backdrop Overlay */}
      {isMobileOpen && (
        <div
          onClick={closeMobile}
          className="fixed inset-0 bg-black/60 backdrop-blur-sm z-40 lg:hidden transition-opacity"
          aria-hidden="true"
        />
      )}

      {/* Sidebar Container */}
      <aside
        className={`w-64 bg-black/35 border-r border-white/15 flex flex-col shrink-0 h-screen fixed top-0 left-0 backdrop-blur z-40 transition-transform duration-300 ease-in-out ${
          isMobileOpen ? "translate-x-0 shadow-2xl" : "-translate-x-full lg:translate-x-0"
        }`}
      >
        {/* Brand Header */}
        <div className="p-5 border-b border-white/10 flex items-center justify-between">
          <Link href="/" onClick={closeMobile} className="flex items-center gap-3">
            <div className="h-9 w-9 rounded-lg bg-gradient-to-tr from-amber-600 to-orange-500 flex items-center justify-center shadow-lg shadow-black/20">
              <Terminal className="h-5 w-5 text-white" />
            </div>
            <div>
              <h1 className="font-semibold text-white text-sm tracking-tight">JTS PowerTool</h1>
              <p className="text-[11px] text-white/70 font-mono">Agent Console</p>
            </div>
          </Link>
          <div className="flex items-center gap-2">
            <span className="flex h-2 w-2 relative">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
            </span>
            <button
              type="button"
              onClick={closeMobile}
              className="p-1 rounded-lg text-white/60 hover:text-white hover:bg-white/10 lg:hidden transition"
              aria-label="Close navigation"
            >
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Navigation Links */}
        <nav className="p-3 space-y-1 flex-1 overflow-y-auto">
          <div className="px-3 py-2 text-[11px] font-semibold text-white/60 uppercase tracking-wider">
            Management
          </div>
          {visibleNavItems.map((item) => {
            const Icon = item.icon;
            const isActive = pathname === item.href;

            // Render Organization & Users as an interactive 2-part dropdown for Admin
            if (item.href === "/users") {
              const isGroupActive =
                pathname === "/users" ||
                pathname.startsWith("/users/") ||
                pathname === "/organizations" ||
                pathname.startsWith("/organizations/");
              const isOrgsActive = pathname === "/organizations" || pathname.startsWith("/organizations/");
              const isUsersActive = pathname === "/users" || pathname.startsWith("/users/");

              return (
                <div key="user-mgmt-group" className="space-y-1">
                  <button
                    type="button"
                    onClick={() => setIsUserMgmtOpen((prev) => !prev)}
                    className={`w-full flex items-center justify-between px-3 py-2.5 rounded-lg text-sm font-medium transition-all cursor-pointer ${
                      isGroupActive || isUserMgmtOpen
                        ? "bg-white/15 text-white border border-white/20 shadow-xs"
                        : "text-white/80 hover:text-white hover:bg-white/10"
                    }`}
                  >
                    <div className="flex items-center gap-3">
                      <Icon className={`h-4 w-4 ${isGroupActive || isUserMgmtOpen ? "text-[#088ADA]" : "text-white/70"}`} />
                      <span>{item.label}</span>
                    </div>
                    <ChevronDown
                      className={`h-4 w-4 text-white/70 transition-transform duration-300 ${
                        isUserMgmtOpen ? "rotate-180 text-white" : ""
                      }`}
                    />
                  </button>

                  {/* Smooth Top-to-Bottom Dropdown */}
                  <div
                    className={`grid transition-all duration-300 ease-in-out ${
                      isUserMgmtOpen
                        ? "grid-rows-[1fr] opacity-100"
                        : "grid-rows-[0fr] opacity-0 pointer-events-none"
                    }`}
                  >
                    <div className="overflow-hidden">
                      <div className="ml-3 pl-3 py-1 space-y-1 border-l-2 border-[#088ADA]/60">
                        <Link
                          href="/organizations"
                          onClick={closeMobile}
                          className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs font-medium transition ${
                            isOrgsActive
                              ? "bg-white/25 text-white font-semibold shadow-xs"
                              : "text-white/80 hover:text-white hover:bg-white/15"
                          }`}
                        >
                          <Building2 className="h-3.5 w-3.5 text-[#088ADA] shrink-0" />
                          <span>Manage Organization</span>
                        </Link>

                        <Link
                          href="/users"
                          onClick={closeMobile}
                          className={`flex items-center gap-2.5 px-3 py-2 rounded-lg text-xs font-medium transition ${
                            isUsersActive
                              ? "bg-white/25 text-white font-semibold shadow-xs"
                              : "text-white/80 hover:text-white hover:bg-white/15"
                          }`}
                        >
                          <UserPlus className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
                          <span>Manage Users</span>
                        </Link>
                      </div>
                    </div>
                  </div>
                </div>
              );
            }

            return (
              <Link
                key={item.href}
                href={item.href}
                onClick={closeMobile}
                className={`flex items-center justify-between px-3 py-2.5 rounded-lg text-sm font-medium transition-all ${
                  isActive
                    ? "bg-white/20 text-white border border-white/30 shadow-sm"
                    : "text-white/80 hover:text-white hover:bg-white/10"
                }`}
              >
                <div className="flex items-center gap-3">
                  <Icon className={`h-4 w-4 ${isActive ? "text-white" : "text-white/70"}`} />
                  <span>{item.label}</span>
                </div>
                {item.badge && (
                  <span className="text-[10px] uppercase font-bold px-1.5 py-0.5 rounded bg-emerald-500/20 text-emerald-300 border border-emerald-500/30">
                    {item.badge}
                  </span>
                )}
              </Link>
            );
          })}
        </nav>

        {/* User Account & Logout */}
        <div className="p-3 mx-3 mb-2 bg-black/40 border border-white/15 rounded-xl flex items-center justify-between">
          <div className="flex items-center gap-2.5 min-w-0">
            <div className="h-7 w-7 rounded-lg bg-white/15 border border-white/20 flex items-center justify-center text-white shrink-0">
              <User className="h-4 w-4" />
            </div>
            <div className="min-w-0">
              <div className="text-xs font-semibold text-white truncate">{username}</div>
              <div className="text-[10px] text-emerald-400 font-mono flex items-center gap-1">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400"></span>
                {roleLabel}
              </div>
            </div>
          </div>
          <button
            onClick={() => logout()}
            className="p-1.5 rounded-lg text-white/50 hover:text-rose-400 hover:bg-rose-950/30 transition shrink-0"
            title="Sign Out"
          >
            <LogOut className="h-4 w-4" />
          </button>
        </div>

        {/* Footer Info (JTS Master Admin Only) */}
        {normRole === "jts_admin" && (
          <div className="p-4 border-t border-white/15/80 bg-black/25">
            <div className="flex items-center justify-between text-xs text-white/50">
              <span>Backend API</span>
              <span className="text-emerald-400 font-mono flex items-center gap-1.5">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400"></span>
                127.0.0.1:8000
              </span>
            </div>
            <div className="mt-2 text-[11px] text-white/50 flex items-center justify-between">
              <span>Slack &amp; GitHub MCP</span>
              <span className="text-white/50">v0.2.0</span>
            </div>
          </div>
        )}
      </aside>
    </>
  );
}
