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
  Users,
  ListTodo,
  Globe,
  X,
  ChevronDown,
  Building2,
  UserPlus,
  Plug,
} from "lucide-react";
import { useMobileNav } from "@/components/DashboardShell";

type NavItem = {
  label: string;
  href: string;
  icon: React.ComponentType<{ className?: string }>;
  roles: string[];
  hint?: string;
};

const NAV_SECTIONS: Array<{ title: string; items: NavItem[] }> = [
  {
    title: "Main",
    items: [
      { label: "Dashboard", href: "/", icon: LayoutDashboard, roles: ["jts_admin", "client_admin"] },
      { label: "My Activity", href: "/my-tasks", icon: ListTodo, roles: ["client_standard"] },
      { label: "Approvals", href: "/approvals", icon: ShieldCheck, roles: ["jts_admin", "client_admin", "client_standard"], hint: "Review GitHub changes proposed by the bot" },
      { label: "Usage & Billing", href: "/billing", icon: Receipt, roles: ["jts_admin", "client_admin"] },
      { label: "Clients & Channels", href: "/folders", icon: Folder, roles: ["jts_admin", "client_admin"] },
      { label: "Keys & Connections", href: "/client-keys", icon: Plug, roles: ["jts_admin", "client_admin", "client_standard"], hint: "Your own API keys and GitHub connection" },
    ],
  },
  {
    title: "Administration",
    items: [
      { label: "Users & Organizations", href: "/users", icon: Users, roles: ["jts_admin"] },
      { label: "API Keys", href: "/keys", icon: KeyRound, roles: ["jts_admin"] },
      { label: "System Settings", href: "/global-settings", icon: Globe, roles: ["jts_admin"] },
    ],
  },
  {
    title: "Monitoring",
    items: [
      { label: "Activity Log", href: "/logs", icon: Activity, roles: ["jts_admin"] },
      { label: "Conversation Inspector", href: "/context", icon: Cpu, roles: ["jts_admin"] },
      { label: "Database", href: "/database", icon: Database, roles: ["jts_admin"] },
    ],
  },
];

export function Sidebar() {
  const pathname = usePathname();
  const { isMobileOpen, closeMobile } = useMobileNav();
  const [role, setRole] = useState<string>("jts_admin");
  const [isUserMgmtOpen, setIsUserMgmtOpen] = useState(
    pathname.startsWith("/users") || pathname.startsWith("/organizations")
  );

  useEffect(() => {
    if (typeof window !== "undefined") {
      try {
        const u = JSON.parse(sessionStorage.getItem("jts_user") || "{}");
        const savedSim = sessionStorage.getItem("jts_simulated_role");
        setRole(savedSim || u.role || "jts_admin");
      } catch {}
    }
  }, []);

  // Treat 'admin', 'jts_admin', or empty as master admin
  const normRole = ["client_admin", "client_standard"].includes(role) ? role : "jts_admin";

  const isActive = (href: string) => (href === "/" ? pathname === "/" : pathname === href || pathname.startsWith(`${href}/`));

  const linkClass = (active: boolean) =>
    `flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium transition ${
      active ? "bg-white text-[#0778bd] shadow-sm" : "text-white/85 hover:text-white hover:bg-white/10"
    }`;

  return (
    <>
      {isMobileOpen && (
        <div onClick={closeMobile} className="fixed inset-0 bg-black/50 backdrop-blur-sm z-40 lg:hidden" aria-hidden="true" />
      )}

      <aside
        className={`w-64 bg-[#0778bd] flex flex-col shrink-0 h-screen fixed top-0 left-0 z-40 print:hidden transition-transform duration-300 ease-in-out ${
          isMobileOpen ? "translate-x-0 shadow-2xl" : "-translate-x-full lg:translate-x-0"
        }`}
      >
        {/* Brand */}
        <div className="h-14 px-4 border-b border-white/15 flex items-center justify-between">
          <Link href="/" onClick={closeMobile} className="flex items-center gap-2.5">
            <div className="h-8 w-8 rounded-lg bg-white/15 flex items-center justify-center">
              <Terminal className="h-4 w-4 text-white" />
            </div>
            <div className="leading-tight">
              <div className="font-semibold text-white text-sm">JTS PowerTool</div>
              <div className="text-[11px] text-white/70">AI assistant console</div>
            </div>
          </Link>
          <button
            type="button"
            onClick={closeMobile}
            className="p-1 rounded-lg text-white/70 hover:text-white hover:bg-white/10 lg:hidden"
            aria-label="Close navigation"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Navigation */}
        <nav className="p-3 flex-1 overflow-y-auto space-y-5">
          {NAV_SECTIONS.map((section) => {
            const items = section.items.filter((i) => i.roles.includes(normRole));
            if (items.length === 0) return null;
            return (
              <div key={section.title} className="space-y-1">
                <div className="px-3 pb-1 text-[11px] font-semibold text-white/55 uppercase tracking-wider">
                  {section.title}
                </div>
                {items.map((item) => {
                  const Icon = item.icon;

                  // Users & Organizations: two sub-pages
                  if (item.href === "/users") {
                    const groupActive = isActive("/users") || isActive("/organizations");
                    return (
                      <div key="user-mgmt" className="space-y-1">
                        <button
                          type="button"
                          onClick={() => setIsUserMgmtOpen((p) => !p)}
                          aria-expanded={isUserMgmtOpen}
                          className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm font-medium transition ${
                            groupActive ? "bg-white/15 text-white" : "text-white/85 hover:text-white hover:bg-white/10"
                          }`}
                        >
                          <span className="flex items-center gap-3">
                            <Icon className="h-4 w-4" />
                            {item.label}
                          </span>
                          <ChevronDown className={`h-4 w-4 transition-transform ${isUserMgmtOpen ? "rotate-180" : ""}`} />
                        </button>
                        {isUserMgmtOpen && (
                          <div className="ml-4 pl-3 border-l border-white/20 space-y-1">
                            <Link href="/users" onClick={closeMobile} className={linkClass(isActive("/users"))}>
                              <UserPlus className="h-4 w-4" />
                              Users
                            </Link>
                            <Link href="/organizations" onClick={closeMobile} className={linkClass(isActive("/organizations"))}>
                              <Building2 className="h-4 w-4" />
                              Organizations
                            </Link>
                          </div>
                        )}
                      </div>
                    );
                  }

                  return (
                    <Link key={item.href} href={item.href} onClick={closeMobile} className={linkClass(isActive(item.href))} title={item.hint}>
                      <Icon className="h-4 w-4" />
                      {item.label}
                    </Link>
                  );
                })}
              </div>
            );
          })}
        </nav>

        <div className="p-4 border-t border-white/15 text-[11px] text-white/60">
          Need help? Contact your JTS administrator.
        </div>
      </aside>
    </>
  );
}
