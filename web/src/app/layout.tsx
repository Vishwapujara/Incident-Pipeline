import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Providers } from "@/trpc/client";
import "./globals.css";

export const metadata: Metadata = {
  title: "Incident Patterns",
  description:
    "Recurring and worsening failure patterns in GitLab's public production incidents, extracted and explained by an LLM pipeline.",
};

export const viewport: Viewport = { width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
