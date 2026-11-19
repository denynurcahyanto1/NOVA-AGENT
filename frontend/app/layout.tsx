import "./globals.css";
import type { Metadata } from "next";
import { SupabaseAuthProvider } from "@/components/auth-provider";

export const metadata: Metadata = {
  title: "NOVA AGENT",
  description: "Universal AI Agent SaaS Platform",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="id">
      <body>
        <SupabaseAuthProvider>{children}</SupabaseAuthProvider>
      </body>
    </html>
  );
}
