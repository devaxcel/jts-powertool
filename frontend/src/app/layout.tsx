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
  title: "JTS PowerTool • Agent Console & Management",
  description: "Enterprise Slack-Claude & GitHub MCP Agent Control Dashboard",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className="dark">
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased flex bg-[#088ADA] text-gray-800 min-h-screen`}
        style={{
          backgroundColor: "#088ADA",
          color: "#ffffff",
          fontFamily: '"Myriad Pro", "Helvetica", "Arial", "Verdana", "Microsoft JhengHei", "Microsoft Sans Serif", sans-serif',
        }}
      >
        <DashboardShell>{children}</DashboardShell>
      </body>
    </html>
  );
}
