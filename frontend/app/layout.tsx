import type { Metadata } from "next";
import Script from "next/script";
import "./globals.css";
import NavBar from "./components/NavBar";

export const metadata: Metadata = {
  title: "YT Automation Factory",
  description: "Download Reels, Spotlights & more — edit with AI — upload to YouTube.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-theme="dark" suppressHydrationWarning>
      <head>
        {/* Blocking theme script — must run before React hydration to prevent flash */}
        <script dangerouslySetInnerHTML={{ __html: `(function(){var t=localStorage.getItem('theme');document.documentElement.setAttribute('data-theme',t==='light'?'light':'dark');})();` }} />
        {/* puter.js — free frontier AI models, no API key needed */}
        <Script src="https://js.puter.com/v2/" strategy="beforeInteractive" />
      </head>
      <body className="min-h-screen" style={{ background: "var(--bg)", color: "var(--text)" }}>
        <NavBar />
        <div className="mx-auto max-w-3xl px-4 pb-16">{children}</div>
      </body>
    </html>
  );
}
