"use client";

import { Fragment, useEffect, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

interface HistoryItem {
  id: number;
  video_id: string;
  source_url: string;
  platform: string;
  title: string;
  youtube_url: string | null;
  status: string;
  scheduled_at: string | null;
  created_at: string;
}

interface DiskStats { file_count: number; total_mb: number; total_bytes: number; }

interface Analytics {
  title: string;
  views: number;
  likes: number;
  comments: number;
  published_at: string;
}

const STATUS_COLOR: Record<string, string> = {
  uploaded: "text-emerald-400",
  scheduled: "text-amber-400",
  processing: "text-blue-400",
  batch_queued: "text-indigo-400",
  error_file_missing: "text-red-400",
  error_upload_failed: "text-red-400",
  error_download_failed: "text-red-400",
};

const PLATFORM_EMOJI: Record<string, string> = {
  instagram: "📸", snapchat: "👻", tiktok: "🎵", youtube: "▶️", twitter: "🐦", reddit: "🤖", pinterest: "📌",
};

function parseApiDate(value: string | null): Date | null {
  if (!value) return null;
  const normalized = value.includes(" ") && !value.includes("T")
    ? value.replace(" ", "T") + "Z"
    : value;
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

export default function HistoryPage() {
  const [items, setItems] = useState<HistoryItem[]>([]);
  const [disk, setDisk] = useState<DiskStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  // analytics cache: { [historyId]: Analytics | "loading" | "error" }
  const [analyticsCache, setAnalyticsCache] = useState<Record<number, Analytics | "loading" | "error">>({});
  const [expandedAnalytics, setExpandedAnalytics] = useState<Set<number>>(new Set());

  const load = async () => {
    setLoading(true); setErr(null);
    try {
      const [hRes, dRes] = await Promise.all([fetch(`${API}/history`), fetch(`${API}/stats/disk`)]);
      if (!hRes.ok || !dRes.ok) throw new Error("Failed to load history data");
      const h = await hRes.json();
      const d = await dRes.json();
      setItems(h.items ?? []);
      setDisk(d);
    } catch {
      setErr("Failed to load history.");
    } finally { setLoading(false); }
  };

  useEffect(() => { load(); }, []);

  const deleteItem = async (id: number) => {
    setErr(null);
    const res = await fetch(`${API}/history/${id}`, { method: "DELETE" });
    if (!res.ok) {
      setErr("Failed to delete history entry.");
      return;
    }
    setItems(prev => prev.filter(i => i.id !== id));
    setExpandedAnalytics(prev => { const s = new Set(prev); s.delete(id); return s; });
    setAnalyticsCache(prev => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  };

  const toggleAnalytics = async (item: HistoryItem) => {
    const id = item.id;
    const isOpen = expandedAnalytics.has(id);
    if (isOpen) {
      setExpandedAnalytics(prev => { const s = new Set(prev); s.delete(id); return s; });
      return;
    }
    setExpandedAnalytics(prev => new Set(prev).add(id));
    // Already cached
    if (analyticsCache[id]) return;
    // Fetch
    setAnalyticsCache(prev => ({ ...prev, [id]: "loading" }));
    try {
      const res = await fetch(`${API}/analytics/${id}`);
      if (!res.ok) throw new Error("not ok");
      const data: Analytics = await res.json();
      setAnalyticsCache(prev => ({ ...prev, [id]: data }));
    } catch {
      setAnalyticsCache(prev => ({ ...prev, [id]: "error" }));
    }
  };

  return (
    <div className="flex flex-col gap-6 pt-8">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight" style={{ color: "var(--text)" }}>Upload History</h1>
        <p className="mt-1 text-sm" style={{ color: "var(--text-muted)" }}>All past and scheduled uploads.</p>
      </div>

      {/* Disk stats */}
      {disk && (
        <div className="rounded-2xl border p-5 grid grid-cols-3 gap-4 text-center" style={{ borderColor: "var(--border)", background: "var(--surface)" }}>
          <div>
            <p className="text-2xl font-bold text-emerald-400">{disk.file_count}</p>
            <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>Temp files</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-emerald-400">{disk.total_mb} MB</p>
            <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>Disk used</p>
          </div>
          <div>
            <p className="text-2xl font-bold text-emerald-400">{items.length}</p>
            <p className="text-xs mt-1" style={{ color: "var(--text-muted)" }}>Total uploads</p>
          </div>
        </div>
      )}

      {/* Actions */}
      <div className="flex gap-3">
        <button type="button" onClick={load}
          className="rounded-lg border px-4 py-2 text-sm font-medium transition hover:bg-white/5"
          style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}>
          ↻ Refresh
        </button>
      </div>

      {err && <p className="rounded-lg border border-red-800/50 bg-red-950/30 px-3 py-2 text-xs text-red-400">{err}</p>}

      {loading ? (
        <p className="text-sm text-center py-8" style={{ color: "var(--text-muted)" }}>Loading…</p>
      ) : items.length === 0 ? (
        <div className="rounded-2xl border p-12 text-center" style={{ borderColor: "var(--border)", background: "var(--surface)" }}>
          <p className="text-4xl mb-3">📭</p>
          <p className="text-sm" style={{ color: "var(--text-muted)" }}>No uploads yet. Go to Studio to download and upload your first video.</p>
        </div>
      ) : (
        <div className="rounded-2xl border overflow-hidden" style={{ borderColor: "var(--border)" }}>
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b text-left text-xs" style={{ borderColor: "var(--border)", background: "var(--surface2)", color: "var(--text-muted)" }}>
                <th className="px-4 py-3 font-medium">Title</th>
                <th className="px-4 py-3 font-medium">Platform</th>
                <th className="px-4 py-3 font-medium">Status</th>
                <th className="px-4 py-3 font-medium">Date</th>
                <th className="px-4 py-3 font-medium">Link</th>
                <th className="px-4 py-3 font-medium">Analytics</th>
                <th className="px-4 py-3 font-medium"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((item, idx) => {
                const analData = analyticsCache[item.id];
                const isExpanded = expandedAnalytics.has(item.id);
                const canAnalyze = item.status === "uploaded" && !!item.youtube_url;
                const scheduledDate = parseApiDate(item.scheduled_at);
                const createdDate = parseApiDate(item.created_at);
                return (
                  <Fragment key={item.id}>
                    <tr
                      className="border-b transition hover:bg-white/5"
                      style={{ borderColor: "var(--border)", background: idx % 2 === 0 ? "var(--surface)" : "var(--surface2)" }}>
                      <td className="px-4 py-3 max-w-[180px]">
                        <span className="truncate block font-medium" style={{ color: "var(--text)" }}>{item.title || "Untitled"}</span>
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-xs" style={{ color: "var(--text-muted)" }}>
                          {PLATFORM_EMOJI[item.platform] ?? "🌐"} {item.platform || "—"}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span className={`text-xs font-medium ${STATUS_COLOR[item.status] ?? "text-neutral-400"}`}>
                          {item.status}
                          {scheduledDate && item.status === "scheduled" && (
                            <span className="block text-neutral-500 font-normal">
                              {scheduledDate.toLocaleString()}
                            </span>
                          )}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs" style={{ color: "var(--text-muted)" }}>
                        {createdDate ? createdDate.toLocaleDateString() : "â€”"}
                      </td>
                      <td className="px-4 py-3">
                        {item.youtube_url ? (
                          <a href={item.youtube_url} target="_blank" rel="noreferrer"
                            className="text-xs text-emerald-400 underline hover:text-emerald-300">Watch ↗</a>
                        ) : <span className="text-xs" style={{ color: "var(--text-muted)" }}>—</span>}
                      </td>
                      <td className="px-4 py-3">
                        {canAnalyze ? (
                          <button type="button" onClick={() => toggleAnalytics(item)}
                            className="text-xs px-2 py-1 rounded-md border transition hover:bg-white/5"
                            style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}>
                            {isExpanded ? "▲ Hide" : "📊 Stats"}
                          </button>
                        ) : <span className="text-xs" style={{ color: "var(--border)" }}>—</span>}
                      </td>
                      <td className="px-4 py-3">
                        <button type="button" onClick={() => deleteItem(item.id)}
                          className="text-xs text-red-400 hover:text-red-300 transition">Delete</button>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr style={{ background: idx % 2 === 0 ? "var(--surface)" : "var(--surface2)" }}>
                        <td colSpan={7} className="px-4 pb-3">
                          {analData === "loading" ? (
                            <p className="text-xs" style={{ color: "var(--text-muted)" }}>Fetching stats…</p>
                          ) : analData === "error" ? (
                            <p className="text-xs text-red-400">Failed to load analytics.</p>
                          ) : analData ? (
                            <div className="flex gap-6 text-sm py-1">
                              <span title="Views">👁 <strong className="text-emerald-400">{analData.views.toLocaleString()}</strong> views</span>
                              <span title="Likes">👍 <strong className="text-emerald-400">{analData.likes.toLocaleString()}</strong> likes</span>
                              <span title="Comments">💬 <strong className="text-emerald-400">{analData.comments.toLocaleString()}</strong> comments</span>
                              {analData.published_at && (
                                <span title="Published" style={{ color: "var(--text-muted)" }} className="text-xs self-center">
                                  Published {new Date(analData.published_at).toLocaleDateString()}
                                </span>
                              )}
                            </div>
                          ) : null}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
