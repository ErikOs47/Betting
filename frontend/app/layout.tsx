import type { Metadata } from "next";
import "./globals.css";
import { Shell } from "@/components/shell";

export const metadata: Metadata = { title: "NBA Betting Intelligence", description: "Statistische NBA-Wettanalyse (Paper Betting)" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="de">
      <body><Shell>{children}</Shell></body>
    </html>
  );
}
