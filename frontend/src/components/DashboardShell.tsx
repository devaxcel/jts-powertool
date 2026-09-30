"use client";

import React, { useEffect, useState, createContext, useContext } from "react";
import { usePathname } from "next/navigation";
import { Sidebar } from "@/components/Sidebar";
import { Header } from "@/components/Header";

interface MobileNavContextType {
  isMobileOpen: boolean;
  setIsMobileOpen: (open: boolean) => void;
  toggleMobile: () => void;
  closeMobile: () => void;
}

const MobileNavContext = createContext<MobileNavContextType>({
  isMobileOpen: false,
  setIsMobileOpen: () => {},
  toggleMobile: () => {},
  closeMobile: () => {},
});

export const useMobileNav = () => useContext(MobileNavContext);

export function DashboardShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const [isMobileOpen, setIsMobileOpen] = useState(false);
  const isLoginPage = pathname === "/login";
  const isSetPasswordPage = pathname === "/set-password";
  const isPublicPage = isLoginPage || isSetPasswordPage;

  // Auto-close mobile sidebar when navigating between pages
  useEffect(() => {
    setIsMobileOpen(false);
  }, [pathname]);

  useEffect(() => {
    if (typeof window === "undefined") return;

    // Purge legacy domain-wide cookies and localStorage so browsers never share sessions across tabs
    document.cookie = "jts_session=; Path=/; Expires=Thu, 01 Jan 1970 00:00:01 GMT;";
    try {
      localStorage.removeItem("jts_user");
      localStorage.removeItem("jts_token");
      localStorage.removeItem("jts_simulated_role");
    } catch {}

    const user = sessionStorage.getItem("jts_user");
    const token = sessionStorage.getItem("jts_token");

    if (isSetPasswordPage) {
      // User is on the public set-password link, do not redirect
      return;
    }

    if (isLoginPage) {
      if (user || token) {
        try {
          const parsedUser = user ? JSON.parse(user) : null;
          if (parsedUser?.role === "client_standard") {
            window.location.href = "/my-tasks";
          } else {
            window.location.href = "/";
          }
        } catch {
          window.location.href = "/";
        }
      }
      return;
    }

    // Tab-Isolated Session Check: Check current tab's sessionStorage
    if (!user && !token) {
      window.location.href = "/login";
      return;
    }

    // Sync latest user profile, global settings, and timezone from DB
    if (token || user) {
      import("@/lib/api").then(({ fetchMyProfile, fetchGlobalSettings }) => {
        fetchMyProfile()
          .then((p) => {
            if (p && p.timezone) {
              sessionStorage.setItem("jts_user_timezone", p.timezone);
              localStorage.setItem("jts_user_timezone", p.timezone);
              const cur = sessionStorage.getItem("jts_user");
              if (cur) {
                try {
                  const u = JSON.parse(cur);
                  sessionStorage.setItem("jts_user", JSON.stringify({ ...u, ...p }));
                } catch {}
              }
            }
          })
          .catch(() => {});

        fetchGlobalSettings()
          .then((gs) => {
            if (gs) {
              if (gs.timezone) {
                localStorage.setItem("jts_global_timezone", gs.timezone);
                sessionStorage.setItem("jts_global_timezone", gs.timezone);
              }
              if (gs.date_format) localStorage.setItem("jts_date_format", gs.date_format);
              if (gs.time_format) localStorage.setItem("jts_time_format", gs.time_format);
              if (gs.show_seconds !== undefined) localStorage.setItem("jts_show_seconds", String(gs.show_seconds));
              if (gs.sync_alerts !== undefined) localStorage.setItem("jts_sync_alerts", String(gs.sync_alerts));
            }
          })
          .catch(() => {});
      });
    }
  }, [pathname, isLoginPage, isSetPasswordPage]);

  if (isPublicPage) {
    return <main className="flex-1 w-full min-h-screen">{children}</main>;
  }

  return (
    <MobileNavContext.Provider
      value={{
        isMobileOpen,
        setIsMobileOpen,
        toggleMobile: () => setIsMobileOpen((prev) => !prev),
        closeMobile: () => setIsMobileOpen(false),
      }}
    >
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0 ml-0 lg:ml-64 bg-[#f5f7fa] h-screen overflow-hidden text-gray-800">
        <Header />
        <main className="flex-1 min-h-0 overflow-y-auto p-4 sm:p-6 lg:p-8">
          {children}
        </main>
      </div>
    </MobileNavContext.Provider>
  );
}
