"use client";

import { useCallback, useMemo, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Privacy = "public" | "unlisted" | "private";
type OverlayPosition =
  | "top"
  | "bottom"
  | "center"
  | "top-left"
  | "top-right"
  | "bottom-left"
  | "bottom-right";

interface TextOverlay {
  id: string;
  text: string;
  position: OverlayPosition;
  font_size: number;
  color: string;
  start_sec: number;
  end_sec: number;
}

function makeOverlay(duration: number): TextOverlay {
  return {
    id: Math.random().toString(36).slice(2),
    text: "",
    position: "bottom",
    font_size: 48,
    color: "#ffffff",
    start_sec: 0,
    end_sec: Math.min(5, duration > 0 ? duration : 5),
  };
}

async function parseApiError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (data && typeof data.detail === "string") return data.detail;
  } catch {
    /* ignore */
  }
  return res.statusText || "Request failed";
}

// ---------------------------------------------------------------------------
// Reusable UI Atoms
// ---------------------------------------------------------------------------

function Label({ children }: { children: React.ReactNode }) {
  return (
    <span className="block text-xs font-medium text-neutral-400 mb-1">
      {children}
    </span>
  );
}

function SectionCard({
  title,
  subtitle,
  disabled,
  children,
}: {
  title: string;
  subtitle?: string;
  disabled?: boolean;
  children: React.ReactNode;
}) {
  return (
    <section
      className={`rounded-2xl border border-neutral-800 bg-neutral-950/80 p-6 shadow-xl backdrop-blur-sm transition-opacity ${
        disabled ? "opacity-40 pointer-events-none" : ""
      }`}
    >
      <h2 className="text-base font-semibold tracking-tight text-emerald-400">
        {title}
      </h2>
      {subtitle && (
        <p className="mt-1 text-xs text-neutral-500">{subtitle}</p>
      )}
      <div className="mt-5 space-y-4">{children}</div>
    </section>
  );
}

function Input({
  label,
  ...props
}: { label?: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="flex flex-col gap-1">
      {label && <Label>{label}</Label>}
      <input
        {...props}
        className={
          "rounded-lg border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none " +
          "focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 disabled:opacity-50 " +
          (props.className ?? "")
        }
      />
    </label>
  );
}

function Slider({
  label,
  value,
  min,
  max,
  step,
  onChange,
  disabled,
  display,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (v: number) => void;
  disabled?: boolean;
  display?: string;
}) {
  return (
    <label className="flex flex-col gap-1">
      <Label>
        {label}: <span className="text-neutral-200">{display ?? value}</span>
      </Label>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        disabled={disabled}
        className="accent-emerald-500 disabled:opacity-50"
      />
    </label>
  );
}

function Btn({
  children,
  variant = "primary",
  ...props
}: { variant?: "primary" | "ghost" | "danger" } & React.ButtonHTMLAttributes<HTMLButtonElement>) {
  const base =
    "rounded-lg px-4 py-2 text-sm font-medium transition disabled:cursor-not-allowed disabled:opacity-40";
  const styles = {
    primary: `${base} bg-emerald-600 text-white hover:bg-emerald-500`,
    ghost: `${base} border border-neutral-600 text-neutral-200 hover:bg-neutral-800`,
    danger: `${base} border border-red-800 text-red-400 hover:bg-red-950`,
  };
  return (
    <button type="button" {...props} className={`${styles[variant]} ${props.className ?? ""}`}>
      {children}
    </button>
  );
}

function ErrorMsg({ msg }: { msg: string | null }) {
  if (!msg) return null;
  return (
    <p className="mt-3 rounded-lg border border-red-800 bg-red-950/40 px-3 py-2 text-xs text-red-400">
      {msg}
    </p>
  );
}

function Toggle({
  label,
  checked,
  onChange,
  disabled,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
  disabled?: boolean;
}) {
  return (
    <label className="flex cursor-pointer items-center gap-3 select-none">
      <span className="text-xs text-neutral-400">{label}</span>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors disabled:opacity-40 ${
          checked ? "bg-emerald-600" : "bg-neutral-700"
        }`}
      >
        <span
          className={`inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
            checked ? "translate-x-6" : "translate-x-1"
          }`}
        />
      </button>
    </label>
  );
}

// ---------------------------------------------------------------------------
// Overlay Card
// ---------------------------------------------------------------------------

function OverlayCard({
  overlay,
  index,
  maxDur,
  disabled,
  onChange,
  onRemove,
}: {
  overlay: TextOverlay;
  index: number;
  maxDur: number;
  disabled: boolean;
  onChange: (id: string, patch: Partial<TextOverlay>) => void;
  onRemove: (id: string) => void;
}) {
  const update = (patch: Partial<TextOverlay>) => onChange(overlay.id, patch);

  return (
    <div className="rounded-xl border border-neutral-800 bg-neutral-900/60 p-4 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-neutral-300">
          Overlay #{index + 1}
        </span>
        <Btn variant="danger" onClick={() => onRemove(overlay.id)} disabled={disabled}>
          Remove
        </Btn>
      </div>

      <Input
        label="Text"
        type="text"
        value={overlay.text}
        onChange={(e) => update({ text: e.target.value })}
        placeholder="Enter overlay text…"
        disabled={disabled}
      />

      <div className="grid grid-cols-2 gap-3">
        <label className="flex flex-col gap-1">
          <Label>Position</Label>
          <select
            value={overlay.position}
            onChange={(e) => update({ position: e.target.value as OverlayPosition })}
            disabled={disabled}
            className="rounded-lg border border-neutral-700 bg-neutral-900 px-2 py-2 text-sm text-neutral-200 disabled:opacity-50"
          >
            {(
              [
                "top",
                "bottom",
                "center",
                "top-left",
                "top-right",
                "bottom-left",
                "bottom-right",
              ] as OverlayPosition[]
            ).map((p) => (
              <option key={p} value={p}>
                {p.replace("-", " ").replace(/\b\w/g, (c) => c.toUpperCase())}
              </option>
            ))}
          </select>
        </label>

        <label className="flex flex-col gap-1">
          <Label>Color</Label>
          <input
            type="color"
            value={overlay.color}
            onChange={(e) => update({ color: e.target.value })}
            disabled={disabled}
            className="h-10 w-full cursor-pointer rounded border border-neutral-700 bg-neutral-900 disabled:opacity-50"
          />
        </label>
      </div>

      <Slider
        label="Font size"
        value={overlay.font_size}
        min={16}
        max={120}
        step={1}
        display={`${overlay.font_size}px`}
        onChange={(v) => update({ font_size: v })}
        disabled={disabled}
      />

      <div className="grid grid-cols-2 gap-3">
        <Input
          label="Start (s)"
          type="number"
          min={0}
          max={maxDur}
          step={0.1}
          value={overlay.start_sec}
          onChange={(e) => update({ start_sec: Number(e.target.value) })}
          disabled={disabled}
        />
        <Input
          label="End (s)"
          type="number"
          min={0}
          max={maxDur}
          step={0.1}
          value={overlay.end_sec}
          onChange={(e) => update({ end_sec: Number(e.target.value) })}
          disabled={disabled}
        />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Main Page
// ---------------------------------------------------------------------------

export default function HomePage() {
  // Download state
  const [reelUrl, setReelUrl] = useState("");
  const [videoId, setVideoId] = useState<string | null>(null);
  const [activeVideoId, setActiveVideoId] = useState<string | null>(null);
  const [duration, setDuration] = useState(0);
  const [thumbDataUrl, setThumbDataUrl] = useState<string | null>(null);
  const [platform, setPlatform] = useState<string | null>(null);

  // Editing state
  const [overlays, setOverlays] = useState<TextOverlay[]>([]);
  const [trimStart, setTrimStart] = useState(0);
  const [trimEnd, setTrimEnd] = useState(0);
  const [brightness, setBrightness] = useState(1.0);
  const [contrast, setContrast] = useState(1.0);
  const [saturation, setSaturation] = useState(1.0);
  const [speed, setSpeed] = useState(1.0);
  const [muteAudio, setMuteAudio] = useState(false);
  const [fadeIn, setFadeIn] = useState(0.0);
  const [fadeOut, setFadeOut] = useState(0.0);
  const [watermark, setWatermark] = useState("");

  // Upload state
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tagsRaw, setTagsRaw] = useState("");
  const [privacy, setPrivacy] = useState<Privacy>("public");

  // Status
  const [downloadError, setDownloadError] = useState<string | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [downloadLoading, setDownloadLoading] = useState(false);
  const [editLoading, setEditLoading] = useState(false);
  const [uploadLoading, setUploadLoading] = useState(false);
  const [uploadProgress, setUploadProgress] = useState(0);
  const [youtubeUrl, setYoutubeUrl] = useState<string | null>(null);

  const maxDur = useMemo(() => Math.max(duration, 0.1), [duration]);

  const resetUploadState = useCallback(() => {
    setYoutubeUrl(null);
    setUploadProgress(0);
    setUploadError(null);
  }, []);

  // Overlay helpers
  const addOverlay = () =>
    setOverlays((prev) => [...prev, makeOverlay(duration)]);

  const updateOverlay = (id: string, patch: Partial<TextOverlay>) =>
    setOverlays((prev) =>
      prev.map((ov) => (ov.id === id ? { ...ov, ...patch } : ov))
    );

  const removeOverlay = (id: string) =>
    setOverlays((prev) => prev.filter((ov) => ov.id !== id));

  // ---------------------------------------------------------------------------
  // Handlers
  // ---------------------------------------------------------------------------

  const onDownload = async () => {
    setDownloadError(null);
    setEditError(null);
    setUploadError(null);
    resetUploadState();
    setDownloadLoading(true);
    try {
      const res = await fetch(`${API_BASE}/download`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: reelUrl.trim() }),
      });
      if (!res.ok) throw new Error(await parseApiError(res));
      const data = await res.json();
      setVideoId(data.video_id);
      setActiveVideoId(data.video_id);
      const d = Number(data.duration) || 0;
      setDuration(d);
      setTrimStart(0);
      setTrimEnd(d > 0 ? d : 60);
      setPlatform(data.platform ?? null);
      // Reset editing state for new video
      setOverlays([]);
      setBrightness(1.0);
      setContrast(1.0);
      setSaturation(1.0);
      setSpeed(1.0);
      setMuteAudio(false);
      setFadeIn(0);
      setFadeOut(0);
      setWatermark("");
      setThumbDataUrl(
        data.thumbnail ? `data:image/jpeg;base64,${data.thumbnail}` : null
      );
    } catch (e) {
      setDownloadError(e instanceof Error ? e.message : "Download failed");
      setVideoId(null);
      setActiveVideoId(null);
      setThumbDataUrl(null);
    } finally {
      setDownloadLoading(false);
    }
  };

  const onApplyEdits = async () => {
    if (!videoId) return;
    setEditError(null);
    setUploadError(null);
    resetUploadState();
    setEditLoading(true);

    const hasTrim =
      trimEnd > trimStart && (trimStart > 0 || trimEnd < duration);
    const hasColor =
      Math.abs(brightness - 1) > 0.01 ||
      Math.abs(contrast - 1) > 0.01 ||
      Math.abs(saturation - 1) > 0.01;
    const hasSpeed = Math.abs(speed - 1.0) > 0.01;
    const hasFade = fadeIn > 0 || fadeOut > 0;
    const hasWatermark = watermark.trim().length > 0;
    const validOverlays = overlays.filter((o) => o.text.trim());

    if (
      !hasTrim &&
      !hasColor &&
      !hasSpeed &&
      !muteAudio &&
      !hasFade &&
      !hasWatermark &&
      validOverlays.length === 0
    ) {
      setEditError("Enable at least one edit option before applying.");
      setEditLoading(false);
      return;
    }

    try {
      const body = {
        video_id: videoId,
        text_overlays: validOverlays.map(({ id: _id, ...rest }) => rest),
        trim: hasTrim ? { start_sec: trimStart, end_sec: trimEnd } : null,
        color_grade: hasColor ? { brightness, contrast, saturation } : null,
        speed,
        mute_audio: muteAudio,
        fade_in_sec: fadeIn,
        fade_out_sec: fadeOut,
        watermark_text: watermark.trim(),
      };
      const res = await fetch(`${API_BASE}/edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await parseApiError(res));
      const data = await res.json();
      setActiveVideoId(data.edited_video_id);
    } catch (e) {
      setEditError(e instanceof Error ? e.message : "Edit failed");
    } finally {
      setEditLoading(false);
    }
  };

  const onSkipEdits = () => {
    setEditError(null);
    setUploadError(null);
    resetUploadState();
    setActiveVideoId(videoId);
  };

  const onUpload = async () => {
    if (!activeVideoId) return;
    setUploadError(null);
    setUploadLoading(true);
    setUploadProgress(8);
    const tick = window.setInterval(() => {
      setUploadProgress((p) => (p >= 92 ? p : p + 6));
    }, 400);
    try {
      const tags = tagsRaw
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      const res = await fetch(`${API_BASE}/upload`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          video_id: activeVideoId,
          title: title.trim() || "Untitled",
          description: description.trim(),
          tags,
          privacy,
        }),
      });
      if (!res.ok) throw new Error(await parseApiError(res));
      const data = await res.json();
      setYoutubeUrl(data.youtube_url);
      setUploadProgress(100);
    } catch (e) {
      setUploadError(e instanceof Error ? e.message : "Upload failed");
      setUploadProgress(0);
    } finally {
      window.clearInterval(tick);
      setUploadLoading(false);
    }
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  return (
    <main className="mx-auto flex max-w-2xl flex-col gap-6 px-4 py-12">
      {/* Header */}
      <header className="space-y-1">
        <h1 className="text-2xl font-bold tracking-tight text-white">
          YT Automation Factory
        </h1>
        <p className="text-sm text-neutral-500">
          Download a Reel or Spotlight, edit it, upload to YouTube — all in
          one place.
        </p>
      </header>

      {/* ── Step 1: Download ── */}
      <SectionCard
        title="Step 1 — Import"
        subtitle="Paste an Instagram Reel or Snapchat Spotlight URL."
      >
        <Input
          type="url"
          value={reelUrl}
          onChange={(e) => setReelUrl(e.target.value)}
          placeholder="instagram.com/reel/… or snapchat.com/spotlight/…"
          onKeyDown={(e) => e.key === "Enter" && onDownload()}
        />
        <div className="flex flex-wrap gap-2 text-xs text-neutral-600">
          <span className="rounded border border-neutral-800 bg-neutral-900 px-2 py-1">
            📸 Instagram Reels
          </span>
          <span className="rounded border border-neutral-800 bg-neutral-900 px-2 py-1">
            👻 Snapchat Spotlights
          </span>
        </div>
        <Btn onClick={onDownload} disabled={downloadLoading || !reelUrl.trim()}>
          {downloadLoading ? "Downloading…" : "Download"}
        </Btn>
        <ErrorMsg msg={downloadError} />

        {thumbDataUrl && videoId && (
          <div className="flex gap-4 pt-1">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={thumbDataUrl}
              alt="Video thumbnail"
              className="h-28 w-auto rounded-lg border border-neutral-800 object-cover shadow"
            />
            <div className="flex flex-col justify-center text-xs text-neutral-400 gap-1">
              {platform && (
                <span className="inline-flex items-center gap-1 text-emerald-400 font-medium">
                  {platform === "instagram" ? "📸 Instagram" : "👻 Snapchat"}
                </span>
              )}
              <span>Duration: <b className="text-neutral-200">{duration.toFixed(1)}s</b></span>
              <span>ID: {videoId.slice(0, 8)}…</span>
            </div>
          </div>
        )}
      </SectionCard>

      {/* ── Step 2: Edit ── */}
      <SectionCard
        title="Step 2 — Edit (optional)"
        subtitle="Apply advanced edits. All operations are processed server-side by ffmpeg."
        disabled={!videoId}
      >
        {/* Trim */}
        <div className="space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-widest text-neutral-500">
            Trim
          </h3>
          <Slider
            label="Start"
            value={trimStart}
            min={0}
            max={maxDur}
            step={0.1}
            display={`${trimStart.toFixed(1)}s`}
            onChange={(v) => {
              setTrimStart(v);
              if (v >= trimEnd) setTrimEnd(Math.min(maxDur, v + 0.1));
            }}
            disabled={!videoId}
          />
          <Slider
            label="End"
            value={trimEnd}
            min={0}
            max={maxDur}
            step={0.1}
            display={`${trimEnd.toFixed(1)}s`}
            onChange={(v) => {
              setTrimEnd(v);
              if (v <= trimStart) setTrimStart(Math.max(0, v - 0.1));
            }}
            disabled={!videoId}
          />
        </div>

        <hr className="border-neutral-800" />

        {/* Color Grading */}
        <div className="space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-widest text-neutral-500">
            Color Grading
          </h3>
          <Slider
            label="Brightness"
            value={brightness}
            min={0.5}
            max={2.0}
            step={0.05}
            display={`${brightness.toFixed(2)}×`}
            onChange={setBrightness}
            disabled={!videoId}
          />
          <Slider
            label="Contrast"
            value={contrast}
            min={0.5}
            max={2.0}
            step={0.05}
            display={`${contrast.toFixed(2)}×`}
            onChange={setContrast}
            disabled={!videoId}
          />
          <Slider
            label="Saturation"
            value={saturation}
            min={0.0}
            max={3.0}
            step={0.05}
            display={`${saturation.toFixed(2)}×`}
            onChange={setSaturation}
            disabled={!videoId}
          />
        </div>

        <hr className="border-neutral-800" />

        {/* Speed & Audio */}
        <div className="space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-widest text-neutral-500">
            Speed & Audio
          </h3>
          <div className="flex flex-wrap gap-2">
            {[0.25, 0.5, 0.75, 1, 1.25, 1.5, 2, 4].map((s) => (
              <button
                key={s}
                type="button"
                disabled={!videoId}
                onClick={() => setSpeed(s)}
                className={`rounded-lg border px-3 py-1.5 text-xs font-medium transition disabled:opacity-40 ${
                  speed === s
                    ? "border-emerald-500 bg-emerald-600/20 text-emerald-300"
                    : "border-neutral-700 text-neutral-400 hover:border-neutral-500"
                }`}
              >
                {s}×
              </button>
            ))}
          </div>
          <Toggle
            label="Mute audio"
            checked={muteAudio}
            onChange={setMuteAudio}
            disabled={!videoId}
          />
        </div>

        <hr className="border-neutral-800" />

        {/* Fade */}
        <div className="space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-widest text-neutral-500">
            Fade
          </h3>
          <div className="grid grid-cols-2 gap-3">
            <Input
              label="Fade in (s)"
              type="number"
              min={0}
              max={10}
              step={0.1}
              value={fadeIn}
              onChange={(e) => setFadeIn(Number(e.target.value))}
              disabled={!videoId}
            />
            <Input
              label="Fade out (s)"
              type="number"
              min={0}
              max={10}
              step={0.1}
              value={fadeOut}
              onChange={(e) => setFadeOut(Number(e.target.value))}
              disabled={!videoId}
            />
          </div>
        </div>

        <hr className="border-neutral-800" />

        {/* Watermark */}
        <div className="space-y-3">
          <h3 className="text-xs font-semibold uppercase tracking-widest text-neutral-500">
            Corner Watermark
          </h3>
          <Input
            type="text"
            label="Watermark text (shown bottom-right)"
            value={watermark}
            onChange={(e) => setWatermark(e.target.value)}
            placeholder="@YourChannel"
            disabled={!videoId}
          />
        </div>

        <hr className="border-neutral-800" />

        {/* Text Overlays */}
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-xs font-semibold uppercase tracking-widest text-neutral-500">
              Text Overlays ({overlays.length})
            </h3>
            <Btn
              variant="ghost"
              onClick={addOverlay}
              disabled={!videoId}
            >
              + Add Overlay
            </Btn>
          </div>
          {overlays.length === 0 && (
            <p className="text-xs text-neutral-600">
              No overlays yet. Click &quot;Add Overlay&quot; to add text on your video.
            </p>
          )}
          {overlays.map((ov, i) => (
            <OverlayCard
              key={ov.id}
              overlay={ov}
              index={i}
              maxDur={maxDur}
              disabled={!videoId}
              onChange={updateOverlay}
              onRemove={removeOverlay}
            />
          ))}
        </div>

        <ErrorMsg msg={editError} />

        <div className="flex flex-wrap gap-3 pt-1">
          <Btn onClick={onApplyEdits} disabled={!videoId || editLoading}>
            {editLoading ? "Applying…" : "Apply Edits"}
          </Btn>
          <Btn variant="ghost" onClick={onSkipEdits} disabled={!videoId}>
            Skip Edits
          </Btn>
        </div>

        {activeVideoId && activeVideoId !== videoId && (
          <p className="text-xs text-emerald-400 mt-1">
            ✓ Edits applied — ready to upload.
          </p>
        )}
      </SectionCard>

      {/* ── Step 3: Upload ── */}
      <SectionCard
        title="Step 3 — Upload to YouTube"
        subtitle="Fill in metadata and publish."
        disabled={!activeVideoId}
      >
        <Input
          label="Title"
          type="text"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          disabled={!activeVideoId}
          placeholder="My awesome video"
        />

        <label className="flex flex-col gap-1">
          <Label>Description</Label>
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={!activeVideoId}
            rows={3}
            placeholder="Describe your video…"
            className="rounded-lg border border-neutral-700 bg-neutral-900 px-3 py-2 text-sm text-neutral-100 outline-none focus:border-emerald-500 focus:ring-1 focus:ring-emerald-500 disabled:opacity-50 resize-none"
          />
        </label>

        <Input
          label="Tags (comma-separated)"
          type="text"
          value={tagsRaw}
          onChange={(e) => setTagsRaw(e.target.value)}
          disabled={!activeVideoId}
          placeholder="shorts, reel, viral"
        />

        <label className="flex flex-col gap-1">
          <Label>Privacy</Label>
          <select
            value={privacy}
            onChange={(e) => setPrivacy(e.target.value as Privacy)}
            disabled={!activeVideoId}
            className="rounded-lg border border-neutral-700 bg-neutral-900 px-2 py-2 text-sm text-neutral-200 disabled:opacity-50"
          >
            <option value="public">Public</option>
            <option value="unlisted">Unlisted</option>
            <option value="private">Private</option>
          </select>
        </label>

        <Btn onClick={onUpload} disabled={!activeVideoId || uploadLoading}>
          {uploadLoading ? "Uploading…" : "Upload to YouTube"}
        </Btn>

        {uploadLoading && (
          <div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-neutral-800">
              <div
                className="h-full bg-emerald-500 transition-all duration-300"
                style={{ width: `${uploadProgress}%` }}
              />
            </div>
            <p className="mt-1 text-xs text-neutral-500">
              Uploading to YouTube… ({uploadProgress}%)
            </p>
          </div>
        )}

        {youtubeUrl && (
          <p className="text-sm">
            <span className="text-neutral-400">Live: </span>
            <a
              href={youtubeUrl}
              target="_blank"
              rel="noreferrer"
              className="text-emerald-400 underline hover:text-emerald-300"
            >
              {youtubeUrl}
            </a>
          </p>
        )}

        <ErrorMsg msg={uploadError} />
      </SectionCard>
    </main>
  );
}
