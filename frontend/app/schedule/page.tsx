"use client";

import { useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface HistoryItem {
  id: number;
  video_id: string;
  platform: string;
  title: string;
  youtube_url: string | null;
  status: string;
  scheduled_at: string | null;
  created_at: string;
}

interface ModalItem extends HistoryItem {}

const PLATFORM_EMOJI: Record<string, string> = {
  instagram: "📸", snapchat: "👻", tiktok: "🎵", youtube: "▶️",
  twitter: "🐦", reddit: "🤖", pinterest: "📌",
};

const STATUS_COLOR: Record<string, string> = {
  scheduled: "#f59e0b",
  uploaded: "#10b981",
  batch_queued: "#6366f1",
  processing: "#3b82f6",
  error_file_missing: "#ef4444",
  error_upload_failed: "#ef4444",
  error_download_failed: "#ef4444",
};

function parseApiDate(value: string | null): Date | null {
  if (!value) return null;
  const normalized = value.includes(" ") && !value.includes("T")
    ? value.replace(" ", "T") + "Z"
    : value;
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function startOfWeek(date: Date): Date {
  const d = new Date(date);
  const day = d.getDay(); // 0=Sun
  const diff = day === 0 ? -6 : 1 - day; // Monday-based
  d.setDate(d.getDate() + diff);
  d.setHours(0, 0, 0, 0);
  return d;
}

function addDays(date: Date, n: number): Date {
  const d = new Date(date);
  d.setDate(d.getDate() + n);
  return d;
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() &&
    a.getMonth() === b.getMonth() &&
    a.getDate() === b.getDate();
}

export default function SchedulePage() {
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [weekStart, setWeekStart] = useState(() => startOfWeek(new Date()));
  const [modal, setModal] = useState<ModalItem | null>(null);

  useEffect(() => {
    setLoading(true);
    fetch(`${API}/history?limit=200`)
      .then(r => r.json())
      .then(d => setItems(d.items ?? []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  const days = Array.from({ length: 7 }, (_, i) => addDays(weekStart, i));

  const dayLabels = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

  function getItemsForDay(day: Date): HistoryItem[] {
    return items.filter(item => {
      const dateStr = item.scheduled_at || item.created_at;
      const parsed = parseApiDate(dateStr);
      if (!parsed) return false;
      return sameDay(parsed, day);
    });
  }

  const prevWeek = () => setWeekStart(d => addDays(d, -7));
  const nextWeek = () => setWeekStart(d => addDays(d, 7));
  const goToday = () => setWeekStart(startOfWeek(new Date()));

  const weekLabel = `${weekStart.toLocaleDateString("en-US", { month: "short", day: "numeric" })} – ${addDays(weekStart, 6).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" })}`;

  return (
    <div className="flex flex-col gap-6 pt-8">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight" style={{ color: "var(--text)" }}>Upload Schedule</h1>
          <p className="mt-1 text-sm" style={{ color: "var(--text-muted)" }}>7-day calendar view of all scheduled and uploaded items.</p>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={prevWeek}
            className="rounded-lg border px-3 py-1.5 text-sm font-medium transition hover:bg-white/5"
            style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}>← Prev</button>
          <button onClick={goToday}
            className="rounded-lg border px-3 py-1.5 text-sm font-medium transition hover:bg-white/5"
            style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}>Today</button>
          <button onClick={nextWeek}
            className="rounded-lg border px-3 py-1.5 text-sm font-medium transition hover:bg-white/5"
            style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}>Next →</button>
        </div>
      </div>

      <p className="text-sm font-medium" style={{ color: "var(--text-muted)" }}>{weekLabel}</p>

      {loading ? (
        <p className="text-sm text-center py-12" style={{ color: "var(--text-muted)" }}>Loading…</p>
      ) : (
        <div className="grid grid-cols-7 gap-2">
          {/* Day headers */}
          {dayLabels.map((label, i) => {
            const day = days[i];
            const isToday = sameDay(day, new Date());
            return (
              <div key={label} className="text-center">
                <p className="text-xs font-semibold mb-1" style={{ color: "var(--text-muted)" }}>{label}</p>
                <p className={`text-sm font-bold mb-2 w-8 h-8 mx-auto flex items-center justify-center rounded-full ${isToday ? "bg-emerald-600 text-white" : ""}`}
                  style={!isToday ? { color: "var(--text)" } : {}}>
                  {day.getDate()}
                </p>
              </div>
            );
          })}

          {/* Day columns */}
          {days.map((day, i) => {
            const dayItems = getItemsForDay(day);
            return (
              <div key={i}
                className="rounded-xl border min-h-[180px] p-1.5 flex flex-col gap-1"
                style={{ borderColor: "var(--border)", background: "var(--surface)" }}>
                {dayItems.length === 0 ? (
                  <p className="text-xs text-center mt-6" style={{ color: "var(--border)" }}>—</p>
                ) : (
                  dayItems.map(item => {
                    const isImmediate = !item.scheduled_at;
                    const dateStr = item.scheduled_at || item.created_at;
                    const parsedDate = parseApiDate(dateStr);
                    const timeStr = parsedDate ? parsedDate.toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit" }) : "";
                    const color = STATUS_COLOR[item.status] ?? "#6b7280";
                    return (
                      <button key={item.id} onClick={() => setModal(item)}
                        className="w-full text-left rounded-lg p-1.5 text-xs transition hover:opacity-80"
                        style={{ background: color + "22", borderLeft: `3px solid ${color}` }}>
                        <p className="truncate font-medium" style={{ color }}>
                          {isImmediate ? "⚡ " : ""}{PLATFORM_EMOJI[item.platform] ?? "🌐"} {item.title || "Untitled"}
                        </p>
                        {timeStr && <p style={{ color: "var(--text-muted)" }}>{timeStr}</p>}
                      </button>
                    );
                  })
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Legend */}
      <div className="flex flex-wrap gap-4 text-xs" style={{ color: "var(--text-muted)" }}>
        {Object.entries(STATUS_COLOR).map(([s, c]) => (
          <span key={s} className="flex items-center gap-1">
            <span className="inline-block w-2.5 h-2.5 rounded-sm" style={{ background: c }} />
            {s}
          </span>
        ))}
        <span className="flex items-center gap-1">⚡ Immediate upload</span>
      </div>

      {/* Modal */}
      {modal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4"
          style={{ background: "rgba(0,0,0,0.7)" }}
          onClick={() => setModal(null)}>
          <div className="rounded-2xl border p-6 max-w-md w-full"
            style={{ background: "var(--surface)", borderColor: "var(--border)" }}
            onClick={e => e.stopPropagation()}>
            <div className="flex items-start justify-between mb-4">
              <h2 className="text-lg font-bold" style={{ color: "var(--text)" }}>
                {PLATFORM_EMOJI[modal.platform] ?? "🌐"} {modal.title || "Untitled"}
              </h2>
              <button onClick={() => setModal(null)} className="text-xl leading-none" style={{ color: "var(--text-muted)" }}>×</button>
            </div>
            <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
              {[
                ["Status", modal.status],
                ["Platform", modal.platform || "—"],
                ["Scheduled", modal.scheduled_at ? new Date(modal.scheduled_at).toLocaleString() : "—"],
                ["Created", new Date(modal.created_at).toLocaleString()],
              ].map(([k, v]) => (
                <div key={String(k)}>
                  <dt className="text-xs" style={{ color: "var(--text-muted)" }}>{k}</dt>
                  <dd className="font-medium" style={{ color: "var(--text)" }}>{v}</dd>
                </div>
              ))}
            </dl>
            {modal.youtube_url && (
              <a href={modal.youtube_url} target="_blank" rel="noreferrer"
                className="mt-4 inline-block text-sm text-emerald-400 underline hover:text-emerald-300">
                Watch on YouTube ↗
              </a>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
