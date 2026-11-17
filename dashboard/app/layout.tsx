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
      <body className="text-gray-100 antialiased">
        {children}
      </body>
    </html>
  );
}
