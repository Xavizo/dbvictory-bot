import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "DBVictory Bot",
  description: "Bot dashboard",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="pl" className="dark">
      <head>
        {/* Before hydration: maps the desktop window's operations onto window.dbvAuth & co. */}
        {/* eslint-disable-next-line @next/next/no-sync-scripts */}
        <script src="/desktop-bridge.js" />
      </head>
      <body className="text-gray-100 antialiased">
        {children}
      </body>
    </html>
  );
}
