"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

export default function NavBar() {
  const path = usePathname();
  const [dark, setDark] = useState(true);

  useEffect(() => {
    const saved = localStorage.getItem("theme");
    const isDark = saved !== "light";
    setDark(isDark);
    document.documentElement.setAttribute("data-theme", isDark ? "dark" : "light");
  }, []);

  const toggleTheme = () => {
    const next = !dark;
    setDark(next);
    const theme = next ? "dark" : "light";
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
  };

  const linkCls = (href: string) =>
    `text-sm px-3 py-1.5 rounded-lg transition font-medium ${
      path === href
        ? "bg-emerald-600/20 text-emerald-400"
        : "text-neutral-400 hover:text-neutral-200 hover:bg-white/5"
    }`;

  return (
    <nav
      className="sticky top-0 z-50 flex items-center justify-between gap-4 border-b px-6 py-3 backdrop-blur-md"
      style={{ borderColor: "var(--border)", background: "color-mix(in srgb, var(--bg) 80%, transparent)" }}
    >
      <div className="flex items-center gap-2">
        <span className="text-lg font-bold tracking-tight" style={{ color: "var(--text)" }}>
          🎬 YT Factory
        </span>
      </div>

      <div className="flex items-center gap-1">
        <Link href="/" className={linkCls("/")}>Studio</Link>
        <Link href="/history" className={linkCls("/history")}>History</Link>
        <Link href="/schedule" className={linkCls("/schedule")}>Schedule</Link>
      </div>

      <button
        onClick={toggleTheme}
        title="Toggle theme"
        className="rounded-lg border px-3 py-1.5 text-xs font-medium transition hover:bg-white/5"
        style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}
      >
        {dark ? "☀️ Light" : "🌙 Dark"}
      </button>
    </nav>
  );
}
