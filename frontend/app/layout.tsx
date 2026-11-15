import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Shell } from "@/components/shell";

export const metadata: Metadata = {
  title: "NBA Betting Intelligence",
  description: "Statistische NBA-Wettanalyse (Paper Betting)",
  applicationName: "NBA Value",
  appleWebApp: { capable: true, title: "NBA Value", statusBarStyle: "black-translucent" },
  icons: { icon: "/icons/icon-192.png", apple: "/icons/apple-touch-icon.png" },
  formatDetection: { telephone: false },
};

export const viewport: Viewport = { themeColor: "#111110", width: "device-width", initialScale: 1, viewportFit: "cover", colorScheme: "dark" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="de">
      <body><Shell>{children}</Shell></body>
    </html>
  );
}
