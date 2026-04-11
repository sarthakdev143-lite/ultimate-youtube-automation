"use client";

import { useCallback, useMemo, useRef, useState } from "react";

const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Privacy = "public" | "unlisted" | "private";
type Position = "top" | "bottom" | "center" | "top-left" | "top-right" | "bottom-left" | "bottom-right";

interface Overlay { id: string; text: string; position: Position; font_size: number; color: string; start_sec: number; end_sec: number; }
interface QueueItem { id: string; url: string; status: "pending" | "processing" | "done" | "error"; label?: string; error?: string; }

const PLATFORM_EMOJI: Record<string, string> = {
  instagram: "📸", snapchat: "👻", tiktok: "🎵", youtube: "▶️", twitter: "🐦", reddit: "🤖", pinterest: "📌",
};
const POSITIONS: Position[] = ["top", "bottom", "center", "top-left", "top-right", "bottom-left", "bottom-right"];

async function apiError(res: Response): Promise<string> {
  try { const d = await res.json(); if (typeof d.detail === "string") return d.detail; } catch { /**/ }
  return res.statusText || "Request failed";
}

function makeOverlay(dur: number): Overlay {
  return { id: crypto.randomUUID(), text: "", position: "bottom", font_size: 48, color: "#ffffff", start_sec: 0, end_sec: Math.min(5, dur || 5) };
}

// ── Shared UI atoms ──────────────────────────────────────────────────────────

function Card({ title, subtitle, disabled, children }: { title: string; subtitle?: string; disabled?: boolean; children: React.ReactNode }) {
  return (
    <section className={`rounded-2xl border p-6 shadow-lg transition-opacity ${disabled ? "opacity-40 pointer-events-none" : ""}`}
      style={{ borderColor: "var(--border)", background: "var(--surface)" }}>
      <h2 className="text-base font-semibold text-emerald-400">{title}</h2>
      {subtitle && <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>{subtitle}</p>}
      <div className="mt-5 space-y-4">{children}</div>
    </section>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="flex flex-col gap-1"><span className="text-xs font-medium" style={{ color: "var(--text-muted)" }}>{label}</span>{children}</label>;
}

const inputCls = "rounded-lg border px-3 py-2 text-sm outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 disabled:opacity-50 w-full";
const inputStyle = { borderColor: "var(--border)", background: "var(--surface2)", color: "var(--text)" };

function Inp(props: React.InputHTMLAttributes<HTMLInputElement> & { label?: string }) {
  const { label, ...rest } = props;
  const el = <input {...rest} className={`${inputCls} ${rest.className ?? ""}`} style={inputStyle} />;
  return label ? <Field label={label}>{el}</Field> : el;
}

function Btn({ children, variant = "primary", className = "", ...p }: { variant?: "primary" | "ghost" | "danger" | "ai" } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const base = "rounded-lg px-4 py-2 text-sm font-medium transition disabled:opacity-40 disabled:cursor-not-allowed";
  const v = {
    primary: `${base} bg-emerald-600 text-white hover:bg-emerald-500`,
    ghost: `${base} border text-sm hover:bg-white/5`,
    danger: `${base} border border-red-800/50 text-red-400 hover:bg-red-950/30`,
    ai: `${base} bg-gradient-to-r from-violet-600 to-indigo-600 text-white hover:from-violet-500 hover:to-indigo-500`,
  };
  return <button type="button" {...p} className={`${v[variant]} ${className}`} style={variant === "ghost" ? { borderColor: "var(--border)", color: "var(--text-muted)" } : undefined}>{children}</button>;
}

function Toggle({ label, checked, onChange, disabled }: { label: string; checked: boolean; onChange: (v: boolean) => void; disabled?: boolean }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs" style={{ color: "var(--text-muted)" }}>{label}</span>
      <button type="button" role="switch" aria-checked={checked} disabled={disabled} onClick={() => onChange(!checked)}
        className={`relative h-6 w-11 rounded-full transition-colors disabled:opacity-40 ${checked ? "bg-emerald-600" : "bg-neutral-700"}`}>
        <span className={`absolute top-1 h-4 w-4 rounded-full bg-white shadow transition-transform ${checked ? "translate-x-6" : "translate-x-1"}`} />
      </button>
    </div>
  );
}

function SectionHead({ children }: { children: React.ReactNode }) {
  return <h3 className="text-xs font-semibold uppercase tracking-widest pt-1" style={{ color: "var(--text-muted)" }}>{children}</h3>;
}

function Divider() { return <hr style={{ borderColor: "var(--border)" }} />; }

function ErrMsg({ msg }: { msg: string | null }) {
  if (!msg) return null;
  return <p className="rounded-lg border border-red-800/50 bg-red-950/30 px-3 py-2 text-xs text-red-400">{msg}</p>;
}

// ── Overlay card ─────────────────────────────────────────────────────────────

function OverlayCard({ ov, i, maxDur, disabled, onChange, onRemove }: {
  ov: Overlay; i: number; maxDur: number; disabled: boolean;
  onChange: (id: string, p: Partial<Overlay>) => void; onRemove: (id: string) => void;
}) {
  const u = (p: Partial<Overlay>) => onChange(ov.id, p);
  return (
    <div className="rounded-xl border p-4 space-y-3" style={{ borderColor: "var(--border)", background: "var(--surface2)" }}>
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold" style={{ color: "var(--text)" }}>Overlay #{i + 1}</span>
        <Btn variant="danger" onClick={() => onRemove(ov.id)} disabled={disabled}>Remove</Btn>
      </div>
      <Inp label="Text" type="text" value={ov.text} onChange={e => u({ text: e.target.value })} placeholder="Overlay text…" disabled={disabled} />
      <div className="grid grid-cols-2 gap-3">
        <Field label="Position">
          <select value={ov.position} onChange={e => u({ position: e.target.value as Position })} disabled={disabled}
            className="rounded-lg border px-2 py-2 text-sm w-full" style={{ borderColor: "var(--border)", background: "var(--surface2)", color: "var(--text)" }}>
            {POSITIONS.map(p => <option key={p} value={p}>{p.replace("-", " ")}</option>)}
          </select>
        </Field>
        <Field label="Color"><input type="color" value={ov.color} onChange={e => u({ color: e.target.value })} disabled={disabled} className="h-10 w-full cursor-pointer rounded border" style={{ borderColor: "var(--border)", background: "var(--surface2)" }} /></Field>
      </div>
      <Field label={`Font size: ${ov.font_size}px`}><input type="range" min={16} max={120} value={ov.font_size} onChange={e => u({ font_size: +e.target.value })} disabled={disabled} className="w-full accent-emerald-500" /></Field>
      <div className="grid grid-cols-2 gap-3">
        <Inp label="Start (s)" type="number" min={0} max={maxDur} step={0.1} value={ov.start_sec} onChange={e => u({ start_sec: +e.target.value })} disabled={disabled} />
        <Inp label="End (s)" type="number" min={0} max={maxDur} step={0.1} value={ov.end_sec} onChange={e => u({ end_sec: +e.target.value })} disabled={disabled} />
      </div>
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function StudioPage() {
  // Download
  const [url, setUrl] = useState("");
  const [videoId, setVideoId] = useState<string | null>(null);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [duration, setDuration] = useState(0);
  const [thumb, setThumb] = useState<string | null>(null);
  const [platform, setPlatform] = useState<string | null>(null);
  const [origTitle, setOrigTitle] = useState("");
  const [origUploader, setOrigUploader] = useState("");
  const [origDesc, setOrigDesc] = useState("");
  const [origTags, setOrigTags] = useState<string[]>([]);
  const [srcUrl, setSrcUrl] = useState("");

  // Batch queue
  const [batchInput, setBatchInput] = useState("");
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [batching, setBatching] = useState(false);

  // Edit state
  const [overlays, setOverlays] = useState<Overlay[]>([]);
  const [trimStart, setTrimStart] = useState(0);
  const [trimEnd, setTrimEnd] = useState(0);
  const [brightness, setBrightness] = useState(1.0);
  const [contrast, setContrast] = useState(1.0);
  const [saturation, setSaturation] = useState(1.0);
  const [speed, setSpeed] = useState(1.0);
  const [mute, setMute] = useState(false);
  const [fadeIn, setFadeIn] = useState(0);
  const [fadeOut, setFadeOut] = useState(0);
  const [watermark, setWatermark] = useState("");
  const [rotate, setRotate] = useState(0);
  const [flipH, setFlipH] = useState(false);
  const [flipV, setFlipV] = useState(false);
  const [crop916, setCrop916] = useState(false);
  const [autoResize, setAutoResize] = useState(false);
  const [removeSilence, setRemoveSilence] = useState(false);
  const [musicId, setMusicId] = useState("");
  const [musicName, setMusicName] = useState("");
  const musicRef = useRef<HTMLInputElement>(null);

  // Presets
  const [presetName, setPresetName] = useState("");
  const [serverPresets, setServerPresets] = useState<{ id: number; name: string; settings: Record<string, unknown> }[]>([]);
  const [presetsOpen, setPresetsOpen] = useState(false);

  // Upload
  const [title, setTitle] = useState("");
  const [desc, setDesc] = useState("");
  const [tagsRaw, setTagsRaw] = useState("");
  const [privacy, setPrivacy] = useState<Privacy>("public");
  const [scheduledAt, setScheduledAt] = useState("");

  // Status
  const [dlLoading, setDlLoading] = useState(false);
  const [editLoading, setEditLoading] = useState(false);
  const [upLoading, setUpLoading] = useState(false);
  const [upProg, setUpProg] = useState(0);
  const [aiLoading, setAiLoading] = useState(false);
  const [musicLoading, setMusicLoading] = useState(false);
  const [dlErr, setDlErr] = useState<string | null>(null);
  const [editErr, setEditErr] = useState<string | null>(null);
  const [upErr, setUpErr] = useState<string | null>(null);
  const [aiErr, setAiErr] = useState<string | null>(null);
  const [ytUrl, setYtUrl] = useState<string | null>(null);

  const maxDur = useMemo(() => Math.max(duration, 0.1), [duration]);

  // ── helpers ─────────────────────────────────────────────────────────────

  const resetEdit = () => {
    setOverlays([]); setBrightness(1); setContrast(1); setSaturation(1);
    setSpeed(1); setMute(false); setFadeIn(0); setFadeOut(0); setWatermark("");
    setRotate(0); setFlipH(false); setFlipV(false); setCrop916(false);
    setAutoResize(false); setRemoveSilence(false); setMusicId(""); setMusicName("");
  };

  const currentSettings = useCallback(() => ({
    brightness, contrast, saturation, speed, mute, fadeIn, fadeOut,
    watermark, rotate, flipH, flipV, crop916, autoResize, removeSilence,
  }), [brightness, contrast, saturation, speed, mute, fadeIn, fadeOut, watermark, rotate, flipH, flipV, crop916, autoResize, removeSilence]);

  const applySettings = (s: Record<string, unknown>) => {
    if (s.brightness != null) setBrightness(s.brightness as number);
    if (s.contrast != null) setContrast(s.contrast as number);
    if (s.saturation != null) setSaturation(s.saturation as number);
    if (s.speed != null) setSpeed(s.speed as number);
    if (s.mute != null) setMute(s.mute as boolean);
    if (s.fadeIn != null) setFadeIn(s.fadeIn as number);
    if (s.fadeOut != null) setFadeOut(s.fadeOut as number);
    if (s.watermark != null) setWatermark(s.watermark as string);
    if (s.rotate != null) setRotate(s.rotate as number);
    if (s.flipH != null) setFlipH(s.flipH as boolean);
    if (s.flipV != null) setFlipV(s.flipV as boolean);
    if (s.crop916 != null) setCrop916(s.crop916 as boolean);
    if (s.autoResize != null) setAutoResize(s.autoResize as boolean);
    if (s.removeSilence != null) setRemoveSilence(s.removeSilence as boolean);
  };

  // ── download ─────────────────────────────────────────────────────────────

  const doDownload = async (targetUrl: string): Promise<{ video_id: string; duration: number; thumbnail: string; platform: string; title: string; uploader: string; description?: string; tags?: string[] } | null> => {
    const res = await fetch(`${API}/download`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ url: targetUrl.trim() }) });
    if (!res.ok) throw new Error(await apiError(res));
    return res.json();
  };

  const onDownload = async () => {
    setDlErr(null); setEditErr(null); setUpErr(null); setYtUrl(null);
    setDlLoading(true);
    try {
      const data = await doDownload(url);
      if (!data) return;
      const d = Number(data.duration) || 0;
      setVideoId(data.video_id); setActiveId(data.video_id);
      setDuration(d); setTrimStart(0); setTrimEnd(d || 60);
      setPlatform(data.platform); setOrigTitle(data.title); setOrigUploader(data.uploader);
      setOrigDesc(data.description || ""); setOrigTags(data.tags || []);
      setSrcUrl(url.trim());
      setThumb(data.thumbnail ? `data:image/jpeg;base64,${data.thumbnail}` : null);
      resetEdit();
    } catch (e) {
      setDlErr(e instanceof Error ? e.message : "Download failed");
    } finally { setDlLoading(false); }
  };

  // ── batch queue ──────────────────────────────────────────────────────────

  const addToQueue = () => {
    const urls = batchInput.split("\n").map(u => u.trim()).filter(Boolean);
    if (!urls.length) return;
    setQueue(prev => [...prev, ...urls.map(u => ({ id: crypto.randomUUID(), url: u, status: "pending" as const }))]);
    setBatchInput("");
  };

  const processQueue = async () => {
    setBatching(true);
    for (const item of queue) {
      if (item.status !== "pending") continue;
      setQueue(prev => prev.map(q => q.id === item.id ? { ...q, status: "processing" } : q));
      try {
        const data = await doDownload(item.url);
        const label = data?.platform ? `${PLATFORM_EMOJI[data.platform] ?? ""} ${data.title || item.url.slice(0, 40)}` : item.url.slice(0, 40);
        setQueue(prev => prev.map(q => q.id === item.id ? { ...q, status: "done", label } : q));
      } catch (e) {
        setQueue(prev => prev.map(q => q.id === item.id ? { ...q, status: "error", error: e instanceof Error ? e.message : "Failed" } : q));
      }
    }
    setBatching(false);
  };

  // ── music upload ─────────────────────────────────────────────────────────

  const onMusicChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setMusicLoading(true);
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await fetch(`${API}/upload-audio`, { method: "POST", body: fd });
      if (!res.ok) throw new Error(await apiError(res));
      const data = await res.json();
      setMusicId(data.audio_id);
      setMusicName(file.name);
    } catch (e) {
      setEditErr(e instanceof Error ? e.message : "Music upload failed");
    } finally { setMusicLoading(false); }
  };

  // ── edit ─────────────────────────────────────────────────────────────────

  const onApplyEdits = async () => {
    if (!videoId) return;
    setEditErr(null); setUpErr(null); setYtUrl(null); setEditLoading(true);
    const hasTrim = trimEnd > trimStart && (trimStart > 0 || trimEnd < duration);
    const hasColor = Math.abs(brightness - 1) > 0.01 || Math.abs(contrast - 1) > 0.01 || Math.abs(saturation - 1) > 0.01;
    const validOvs = overlays.filter(o => o.text.trim());
    try {
      const res = await fetch(`${API}/edit`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          video_id: videoId,
          text_overlays: validOvs.map(({ id: _id, ...rest }) => rest),
          trim: hasTrim ? { start_sec: trimStart, end_sec: trimEnd } : null,
          color_grade: hasColor ? { brightness, contrast, saturation } : null,
          speed, mute_audio: mute, fade_in_sec: fadeIn, fade_out_sec: fadeOut,
          watermark_text: watermark, rotate, flip_h: flipH, flip_v: flipV,
          crop_9_16: crop916, auto_resize: autoResize, remove_silence: removeSilence,
          music_audio_id: musicId,
        }),
      });
      if (!res.ok) throw new Error(await apiError(res));
      const data = await res.json();
      setActiveId(data.edited_video_id);
    } catch (e) { setEditErr(e instanceof Error ? e.message : "Edit failed"); }
    finally { setEditLoading(false); }
  };

  // ── AI generate with puter.js ────────────────────────────────────────────

  const onAiGenerate = async () => {
    setAiErr(null); setAiLoading(true);
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const puter = (window as any).puter;
      if (!puter?.ai?.chat) throw new Error("puter.js not loaded yet. Refresh and try again.");
      const prompt = `You are a YouTube SEO expert. Given this video info:
Platform: ${platform || "unknown"}
Original title: ${origTitle || "untitled"}
Uploader: ${origUploader || "unknown"}
Duration: ${duration.toFixed(0)}s
Description: ${origDesc || "Not provided"}
Tags: ${origTags.length > 0 ? origTags.join(", ") : "Not provided"}

Generate a JSON object with these exact keys:
- "title": engaging catchy YouTube title with suitable emojis, max 90 characters
- "description": SEO-optimized description
- "tags": array of relevant hashtags (strings, no # symbol)

Respond with ONLY valid JSON, no markdown.`;
      const resp = await puter.ai.chat(prompt);
      const raw = typeof resp === "string" ? resp : (resp?.message?.content?.[0]?.text ?? resp?.message?.content ?? "{}");
      const cleaned = raw.replace(/```json|```/g, "").trim();
      const parsed = JSON.parse(cleaned);
      if (parsed.title) setTitle(parsed.title);
      if (parsed.description) setDesc(parsed.description);
      if (Array.isArray(parsed.tags)) setTagsRaw(parsed.tags.join(", "));
    } catch (e) {
      setAiErr(e instanceof Error ? e.message : "AI generation failed");
    } finally { setAiLoading(false); }
  };

  // ── presets ──────────────────────────────────────────────────────────────

  const loadServerPresets = async () => {
    try {
      const res = await fetch(`${API}/presets`);
      const data = await res.json();
      setServerPresets(data.items ?? []);
    } catch { /**/ }
  };

  const savePreset = async () => {
    if (!presetName.trim()) return;
    await fetch(`${API}/presets`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: presetName.trim(), settings: currentSettings() }) });
    setPresetName(""); loadServerPresets();
  };

  const deletePreset = async (id: number) => {
    await fetch(`${API}/presets/${id}`, { method: "DELETE" });
    loadServerPresets();
  };

  // ── upload ───────────────────────────────────────────────────────────────

  const onUpload = async () => {
    if (!activeId) return;
    setUpErr(null); setYtUrl(null); setUpLoading(true); setUpProg(8);
    const tick = setInterval(() => setUpProg(p => p >= 92 ? p : p + 5), 400);
    try {
      const res = await fetch(`${API}/upload`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          video_id: activeId, title: title.trim() || "Untitled",
          description: desc.trim(),
          tags: tagsRaw.split(",").map(t => t.trim()).filter(Boolean),
          privacy, source_url: srcUrl, platform: platform ?? "",
          scheduled_at: scheduledAt || null,
        }),
      });
      if (!res.ok) throw new Error(await apiError(res));
      const data = await res.json();
      if (data.scheduled) {
        setYtUrl(`[Scheduled] ID: ${data.history_id} at ${data.scheduled_at}`);
      } else {
        setYtUrl(data.youtube_url); setUpProg(100);
      }
    } catch (e) { setUpErr(e instanceof Error ? e.message : "Upload failed"); setUpProg(0); }
    finally { clearInterval(tick); setUpLoading(false); }
  };

  // ── render ───────────────────────────────────────────────────────────────

  return (
    <div className="flex flex-col gap-6 pt-8">

      {/* ── BATCH QUEUE ─────────────────────────────────────────── */}
      <Card title="Batch Queue" subtitle="Paste multiple URLs (one per line) to download them sequentially.">
        <textarea value={batchInput} onChange={e => setBatchInput(e.target.value)}
          rows={3} placeholder={"https://www.tiktok.com/...\nhttps://www.instagram.com/reel/...\nhttps://x.com/user/status/..."}
          className="rounded-lg border px-3 py-2 text-sm resize-none w-full outline-none focus:border-emerald-500"
          style={{ borderColor: "var(--border)", background: "var(--surface2)", color: "var(--text)" }} />
        <div className="flex flex-wrap gap-2">
          <Btn onClick={addToQueue} disabled={!batchInput.trim()}>Add to Queue</Btn>
          <Btn variant="ghost" onClick={processQueue} disabled={batching || !queue.some(q => q.status === "pending")}>
            {batching ? "Processing…" : "Process Queue"}
          </Btn>
          {queue.length > 0 && <Btn variant="danger" onClick={() => setQueue([])}>Clear</Btn>}
        </div>
        {queue.length > 0 && (
          <ul className="space-y-1">
            {queue.map(q => (
              <li key={q.id} className="flex items-start gap-2 text-xs rounded-lg px-3 py-2" style={{ background: "var(--surface2)" }}>
                <span>{q.status === "pending" ? "⏳" : q.status === "processing" ? "⚙️" : q.status === "done" ? "✅" : "❌"}</span>
                <span className="truncate flex-1" style={{ color: "var(--text)" }}>{q.label || q.url}</span>
                {q.error && <span className="text-red-400 shrink-0">{q.error}</span>}
              </li>
            ))}
          </ul>
        )}
      </Card>

      {/* ── STEP 1: DOWNLOAD ────────────────────────────────────── */}
      <Card title="Step 1 — Import" subtitle="Paste a URL from any supported platform.">
        <div className="flex flex-wrap gap-2 text-xs">
          {Object.entries(PLATFORM_EMOJI).map(([p, e]) => (
            <span key={p} className="rounded border px-2 py-1" style={{ borderColor: "var(--border)", color: "var(--text-muted)" }}>{e} {p}</span>
          ))}
        </div>
        <Inp type="url" value={url} onChange={e => setUrl(e.target.value)} placeholder="Paste video URL here…" onKeyDown={e => e.key === "Enter" && onDownload()} />
        <Btn onClick={onDownload} disabled={dlLoading || !url.trim()}>{dlLoading ? "Downloading…" : "Download"}</Btn>
        <ErrMsg msg={dlErr} />
        {thumb && videoId && (
          <div className="flex gap-4 items-center">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={thumb} alt="Thumbnail" className="h-24 w-auto rounded-lg object-cover border" style={{ borderColor: "var(--border)" }} />
            <div className="flex flex-col gap-1 text-xs" style={{ color: "var(--text-muted)" }}>
              {platform && <span className="text-emerald-400 font-medium">{PLATFORM_EMOJI[platform]} {platform}</span>}
              {origTitle && <span className="font-medium line-clamp-2" style={{ color: "var(--text)" }}>{origTitle}</span>}
              {origUploader && <span>by {origUploader}</span>}
              <span>Duration: {duration.toFixed(1)}s</span>
            </div>
          </div>
        )}
        {/* In-browser video preview */}
        {videoId && (
          <div className="pt-1">
            <p className="text-xs mb-1" style={{ color: "var(--text-muted)" }}>Preview</p>
            <video src={`${API}/video/${videoId}/file`} controls className="w-full rounded-lg border max-h-72" style={{ borderColor: "var(--border)" }} />
          </div>
        )}
      </Card>

      {/* ── STEP 2: EDIT ────────────────────────────────────────── */}
      <Card title="Step 2 — Edit (optional)" subtitle="All edits are processed server-side by ffmpeg." disabled={!videoId}>

        {/* Trim */}
        <SectionHead>Trim</SectionHead>
        <Field label={`Start: ${trimStart.toFixed(1)}s`}>
          <input type="range" min={0} max={maxDur} step={0.1} value={trimStart} className="w-full accent-emerald-500"
            onChange={e => { const v = +e.target.value; setTrimStart(v); if (v >= trimEnd) setTrimEnd(Math.min(maxDur, v + 0.1)); }} />
        </Field>
        <Field label={`End: ${trimEnd.toFixed(1)}s`}>
          <input type="range" min={0} max={maxDur} step={0.1} value={trimEnd} className="w-full accent-emerald-500"
            onChange={e => { const v = +e.target.value; setTrimEnd(v); if (v <= trimStart) setTrimStart(Math.max(0, v - 0.1)); }} />
        </Field>

        <Divider />

        {/* Color Grading */}
        <SectionHead>Color Grading</SectionHead>
        {[["Brightness", brightness, setBrightness, 0.5, 2.0], ["Contrast", contrast, setContrast, 0.5, 2.0], ["Saturation", saturation, setSaturation, 0.0, 3.0]].map(([label, val, set, mn, mx]) => (
          <Field key={label as string} label={`${label}: ${(val as number).toFixed(2)}×`}>
            <input type="range" min={mn as number} max={mx as number} step={0.05} value={val as number} className="w-full accent-emerald-500"
              onChange={e => (set as (v: number) => void)(+e.target.value)} />
          </Field>
        ))}

        <Divider />

        {/* Speed */}
        <SectionHead>Playback Speed</SectionHead>
        <div className="flex flex-wrap gap-2">
          {[0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 4].map(s => (
            <button key={s} type="button" onClick={() => setSpeed(s)}
              className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition ${speed === s ? "border-emerald-500 bg-emerald-600/20 text-emerald-300" : "text-neutral-400 hover:border-neutral-500"}`}
              style={speed !== s ? { borderColor: "var(--border)" } : undefined}>{s}×</button>
          ))}
        </div>

        <Divider />

        {/* Transform */}
        <SectionHead>Transform</SectionHead>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Rotate">
            <div className="flex gap-1">
              {[0, 90, 180, 270].map(r => (
                <button key={r} type="button" onClick={() => setRotate(r)}
                  className={`flex-1 rounded-lg border py-1.5 text-xs font-medium transition ${rotate === r ? "border-emerald-500 bg-emerald-600/20 text-emerald-300" : "text-neutral-400"}`}
                  style={rotate !== r ? { borderColor: "var(--border)" } : undefined}>{r}°</button>
              ))}
            </div>
          </Field>
        </div>
        <Toggle label="Flip Horizontal" checked={flipH} onChange={setFlipH} />
        <Toggle label="Flip Vertical" checked={flipV} onChange={setFlipV} />

        <Divider />

        {/* Format */}
        <SectionHead>Format & Sizing</SectionHead>
        <Toggle label="Crop to 9:16 (Shorts / Reels)" checked={crop916} onChange={setCrop916} />
        <Toggle label="Auto-resize to 1080×1920" checked={autoResize} onChange={setAutoResize} />

        <Divider />

        {/* Audio */}
        <SectionHead>Audio</SectionHead>
        <Toggle label="Mute audio" checked={mute} onChange={setMute} />
        <Toggle label="Remove silence" checked={removeSilence} onChange={setRemoveSilence} />
        <Field label="Background music">
          <div className="flex items-center gap-2">
            <Btn variant="ghost" onClick={() => musicRef.current?.click()} disabled={musicLoading}>
              {musicLoading ? "Uploading…" : musicName ? `🎵 ${musicName}` : "Upload MP3"}
            </Btn>
            {musicName && <Btn variant="danger" onClick={() => { setMusicId(""); setMusicName(""); }}>Remove</Btn>}
          </div>
          <input ref={musicRef} type="file" accept="audio/*" className="hidden" onChange={onMusicChange} />
        </Field>

        <Divider />

        {/* Fade */}
        <SectionHead>Fade</SectionHead>
        <div className="grid grid-cols-2 gap-3">
          <Inp label="Fade in (s)" type="number" min={0} max={10} step={0.1} value={fadeIn} onChange={e => setFadeIn(+e.target.value)} />
          <Inp label="Fade out (s)" type="number" min={0} max={10} step={0.1} value={fadeOut} onChange={e => setFadeOut(+e.target.value)} />
        </div>

        <Divider />

        {/* Watermark */}
        <SectionHead>Watermark</SectionHead>
        <Inp label="Corner text (bottom-right)" type="text" value={watermark} onChange={e => setWatermark(e.target.value)} placeholder="@YourChannel" />

        <Divider />

        {/* Text Overlays */}
        <SectionHead>Text Overlays ({overlays.length})</SectionHead>
        {overlays.length === 0 && <p className="text-xs" style={{ color: "var(--text-muted)" }}>No overlays yet.</p>}
        {overlays.map((ov, i) => (
          <OverlayCard key={ov.id} ov={ov} i={i} maxDur={maxDur} disabled={!videoId}
            onChange={(id, p) => setOverlays(prev => prev.map(o => o.id === id ? { ...o, ...p } : o))}
            onRemove={id => setOverlays(prev => prev.filter(o => o.id !== id))} />
        ))}
        <Btn variant="ghost" onClick={() => setOverlays(prev => [...prev, makeOverlay(duration)])}>+ Add Overlay</Btn>

        <Divider />

        {/* Presets */}
        <SectionHead>Edit Presets</SectionHead>
        <Btn variant="ghost" onClick={() => { setPresetsOpen(o => !o); if (!presetsOpen) loadServerPresets(); }}>
          {presetsOpen ? "▲ Hide Presets" : "▼ Show Presets"}
        </Btn>
        {presetsOpen && (
          <div className="space-y-3">
            <div className="flex gap-2">
              <input value={presetName} onChange={e => setPresetName(e.target.value)} placeholder="Preset name…"
                className={`${inputCls} flex-1`} style={inputStyle} />
              <Btn onClick={savePreset} disabled={!presetName.trim()}>Save</Btn>
            </div>
            {serverPresets.length === 0 && <p className="text-xs" style={{ color: "var(--text-muted)" }}>No saved presets.</p>}
            <div className="flex flex-wrap gap-2">
              {serverPresets.map(p => (
                <div key={p.id} className="flex items-center gap-1 rounded-lg border px-2 py-1" style={{ borderColor: "var(--border)" }}>
                  <button type="button" className="text-xs text-emerald-400 hover:text-emerald-300" onClick={() => applySettings(p.settings)}>{p.name}</button>
                  <button type="button" className="text-xs text-red-400 ml-1" onClick={() => deletePreset(p.id)}>×</button>
                </div>
              ))}
            </div>
          </div>
        )}

        <ErrMsg msg={editErr} />
        <div className="flex flex-wrap gap-3 pt-1">
          <Btn onClick={onApplyEdits} disabled={!videoId || editLoading}>{editLoading ? "Applying…" : "Apply Edits"}</Btn>
          <Btn variant="ghost" onClick={() => setActiveId(videoId)} disabled={!videoId}>Skip Edits</Btn>
        </div>
        {activeId && activeId !== videoId && (
          <p className="text-xs text-emerald-400">✓ Edits applied — ready to upload.</p>
        )}
        {activeId && (
          <div className="pt-1">
            <p className="text-xs mb-1" style={{ color: "var(--text-muted)" }}>Edited preview</p>
            <video key={activeId} src={`${API}/video/${activeId}/file`} controls className="w-full rounded-lg border max-h-72" style={{ borderColor: "var(--border)" }} />
          </div>
        )}
      </Card>

      {/* ── STEP 3: UPLOAD ──────────────────────────────────────── */}
      <Card title="Step 3 — Upload to YouTube" subtitle="Fill in metadata and publish." disabled={!activeId}>

        {/* AI Generate button */}
        <div className="flex items-center gap-3">
          <Btn variant="ai" onClick={onAiGenerate} disabled={aiLoading || !videoId}>
            {aiLoading ? "Generating…" : "✨ AI Generate Title & Tags"}
          </Btn>
          <span className="text-xs" style={{ color: "var(--text-muted)" }}>Powered by puter.js (GPT-4o, free)</span>
        </div>
        <ErrMsg msg={aiErr} />

        <Inp label="Title" type="text" value={title} onChange={e => setTitle(e.target.value)} placeholder="Your video title" />
        <Field label="Description">
          <textarea value={desc} onChange={e => setDesc(e.target.value)} rows={3} placeholder="Describe your video…"
            className="rounded-lg border px-3 py-2 text-sm resize-none w-full outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500"
            style={{ borderColor: "var(--border)", background: "var(--surface2)", color: "var(--text)" }} />
        </Field>
        <Inp label="Tags (comma-separated)" type="text" value={tagsRaw} onChange={e => setTagsRaw(e.target.value)} placeholder="shorts, viral, reel" />
        <Field label="Privacy">
          <select value={privacy} onChange={e => setPrivacy(e.target.value as Privacy)}
            className="rounded-lg border px-2 py-2 text-sm w-full"
            style={{ borderColor: "var(--border)", background: "var(--surface2)", color: "var(--text)" }}>
            <option value="public">Public</option>
            <option value="unlisted">Unlisted</option>
            <option value="private">Private</option>
          </select>
        </Field>
        <Inp label="Schedule for later (optional — leave empty for immediate upload)" type="datetime-local" value={scheduledAt} onChange={e => setScheduledAt(e.target.value)} />

        <Btn onClick={onUpload} disabled={!activeId || upLoading}>{upLoading ? "Uploading…" : scheduledAt ? "Schedule Upload" : "Upload to YouTube"}</Btn>

        {upLoading && (
          <div>
            <div className="h-2 w-full overflow-hidden rounded-full" style={{ background: "var(--surface2)" }}>
              <div className="h-full bg-emerald-500 transition-all duration-300" style={{ width: `${upProg}%` }} />
            </div>
            <p className="mt-1 text-xs" style={{ color: "var(--text-muted)" }}>Uploading… ({upProg}%)</p>
          </div>
        )}
        {ytUrl && (
          <p className="text-sm">
            {ytUrl.startsWith("[Scheduled]")
              ? <span className="text-emerald-400">{ytUrl}</span>
              : <><span style={{ color: "var(--text-muted)" }}>Live: </span><a href={ytUrl} target="_blank" rel="noreferrer" className="text-emerald-400 underline hover:text-emerald-300">{ytUrl}</a></>}
          </p>
        )}
        <ErrMsg msg={upErr} />
      </Card>
    </div>
  );
}
