import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { NavBar } from "@/components/NavBar";
import { AuthProvider } from "@/lib/auth-context";

const inter = Inter({
  variable: "--font-inter",
  subsets: ["latin"],
});

const jetbrainsMono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  subsets: ["latin"],
});

const SITE_URL = process.env.NEXT_PUBLIC_SITE_URL || "https://moat-steel.vercel.app";
const SITE_DESCRIPTION =
  "Moat is an equity-research app: a quality-weighted intrinsic-value engine " +
  "(DCF, reverse DCF, Monte Carlo, moat scoring) with a Piotroski F-Score, " +
  "legendary-investor AI analysis, and a personal portfolio tracker.";

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: "Moat — Intrinsic-Value Stock Research",
    template: "%s · Moat",
  },
  description: SITE_DESCRIPTION,
  applicationName: "Moat",
  keywords: [
    "stock valuation", "intrinsic value", "DCF", "equity research",
    "Piotroski F-Score", "value investing", "moat",
  ],
  openGraph: {
    type: "website",
    url: SITE_URL,
    siteName: "Moat",
    title: "Moat — Intrinsic-Value Stock Research",
    description: SITE_DESCRIPTION,
    images: [{ url: "/og.svg", width: 1200, height: 630, alt: "Moat" }],
  },
  twitter: {
    card: "summary_large_image",
    title: "Moat — Intrinsic-Value Stock Research",
    description: SITE_DESCRIPTION,
    images: ["/og.svg"],
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${jetbrainsMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col font-sans">
        <AuthProvider>
          <NavBar />
          {children}
        </AuthProvider>
      </body>
    </html>
  );
}
