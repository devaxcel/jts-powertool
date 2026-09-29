import type { Metadata } from "next";
import localFont from "next/font/local";
import "./globals.css";

const geistSans = localFont({
  src: "./fonts/GeistVF.woff",
  variable: "--font-geist-sans",
  weight: "100 900",
});
const geistMono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-geist-mono",
  weight: "100 900",
});

import { DashboardShell } from "@/components/DashboardShell";

export const metadata: Metadata = {
  title: "JTS PowerTool",
  description: "Manage your Slack AI assistant, approvals, usage and billing",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased flex bg-[#f5f7fa] text-gray-800 min-h-screen`}
        style={{
          backgroundColor: "#f5f7fa",
          color: "#1f2937",
          fontFamily: '"Myriad Pro", "Helvetica", "Arial", "Verdana", "Microsoft JhengHei", "Microsoft Sans Serif", sans-serif',
        }}
      >
        <DashboardShell>{children}</DashboardShell>
      </body>
    </html>
  );
}
