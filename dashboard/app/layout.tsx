import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = { title: "DBVictory Bot", description: "Panel bota DBVictory" };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="pl" className="dark">
      <head>
        {/* Before hydration: maps the desktop window's operations onto window.dbvAuth & co. */}
        {/* eslint-disable-next-line @next/next/no-sync-scripts */}
        <script src="/desktop-bridge.js" />
      </head>
      <body className="text-gray-100 antialiased">{children}</body>
    </html>
  );
}
