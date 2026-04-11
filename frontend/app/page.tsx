"use client";

import { useCallback, useMemo, useState } from "react";

const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

type Privacy = "public" | "unlisted" | "private";

async function parseApiError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (data && typeof data.detail === "string") return data.detail;
  } catch {
    /* ignore */
  }
  return res.statusText || "Request failed";
}

export default function HomePage() {
  const [reelUrl, setReelUrl] = useState("");
  const [videoId, setVideoId] = useState<string | null>(null);
  const [activeVideoId, setActiveVideoId] = useState<string | null>(null);
  const [duration, setDuration] = useState(0);
  const [thumbDataUrl, setThumbDataUrl] = useState<string | null>(null);

  const [overlayText, setOverlayText] = useState("");
  const [position, setPosition] = useState<"top" | "center" | "bottom">("bottom");
  const [color, setColor] = useState("#ffffff");
  const [fontSize, setFontSize] = useState(48);
  const [overlayStart, setOverlayStart] = useState(0);
  const [overlayEnd, setOverlayEnd] = useState(5);

  const [trimStart, setTrimStart] = useState(0);
  const [trimEnd, setTrimEnd] = useState(0);

  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tagsRaw, setTagsRaw] = useState("");
  const [privacy, setPrivacy] = useState<Privacy>("public");

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
      setDuration(Number(data.duration) || 0);
      const d = Number(data.duration) || 0;
      setTrimEnd(d > 0 ? d : 60);
      setOverlayEnd(Math.min(5, d > 0 ? d : 5));
      setOverlayStart(0);
      if (data.thumbnail) {
        setThumbDataUrl(`data:image/jpeg;base64,${data.thumbnail}`);
      } else {
        setThumbDataUrl(null);
      }
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
    const hasText = overlayText.trim().length > 0;
    const hasTrim = trimEnd > trimStart && (trimStart > 0 || trimEnd < duration);
    if (!hasText && !hasTrim) {
      setEditError("Add text overlay and/or adjust trim (end must exceed start).");
      setEditLoading(false);
      return;
    }
    try {
      const body = {
        video_id: videoId,
        text_overlay: hasText
          ? {
            text: overlayText,
            position,
            font_size: fontSize,
            color,
            start_sec: overlayStart,
            end_sec: overlayEnd,
          }
          : null,
        trim: hasTrim
          ? { start_sec: trimStart, end_sec: trimEnd }
          : null,
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

  return (
    <main className="mx-auto flex max-w-2xl flex-col gap-8 px-4 py-12">
      <header className="space-y-2">
        <h1 className="text-2xl font-semibold tracking-tight text-white">
          YT Automation Factory
        </h1>
        <p className="text-sm text-neutral-400">
          Import an Instagram Reel, optionally edit, then upload to YouTube.
        </p>
      </header>

      <section className="rounded-xl border border-neutral-800 bg-neutral-950/80 p-6 shadow-lg">
        <h2 className="text-lg font-medium text-success">Step 1 — Import</h2>
        <p className="mt-1 text-sm text-neutral-500">
          Paste a public Reel URL (cookies required on the server).
        </p>
        <input
          type="url"
          value={reelUrl}
          onChange={(e) => setReelUrl(e.target.value)}
          placeholder="https://www.instagram.com/reel/..."
          className="mt-4 w-full rounded-lg border border-neutral-700 bg-surface px-3 py-2 text-sm outline-none ring-accent focus:border-accent focus:ring-1"
        />
        <button
          type="button"
          onClick={onDownload}
          disabled={downloadLoading || !reelUrl.trim()}
          className="mt-4 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {downloadLoading ? "Downloading…" : "Download"}
        </button>
        {downloadError && (
          <p className="mt-3 text-sm text-accent">{downloadError}</p>
        )}
        {thumbDataUrl && videoId && (
          <div className="mt-6 flex gap-4">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={thumbDataUrl}
              alt="Video thumbnail"
              className="h-28 w-auto rounded-md border border-neutral-800 object-cover"
            />
            <div className="text-sm text-neutral-300">
              <p>Duration: {duration.toFixed(1)}s</p>
              <p className="mt-1 text-neutral-500">Video ID: {videoId.slice(0, 8)}…</p>
            </div>
          </div>
        )}
      </section>

      <section
        className={`rounded-xl border border-neutral-800 bg-neutral-950/80 p-6 shadow-lg ${!videoId ? "opacity-50" : ""
          }`}
      >
        <h2 className="text-lg font-medium text-success">Step 2 — Edit (optional)</h2>
        <div className="mt-4 space-y-6">
          <div>
            <h3 className="text-sm font-medium text-neutral-300">Text overlay</h3>
            <input
              type="text"
              value={overlayText}
              onChange={(e) => setOverlayText(e.target.value)}
              disabled={!videoId}
              placeholder="Overlay text"
              className="mt-2 w-full rounded-lg border border-neutral-700 bg-surface px-3 py-2 text-sm outline-none focus:border-accent focus:ring-1 focus:ring-accent disabled:opacity-50"
            />
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <label className="flex flex-col gap-1 text-xs text-neutral-400">
                Position
                <select
                  value={position}
                  onChange={(e) =>
                    setPosition(e.target.value as "top" | "center" | "bottom")
                  }
                  disabled={!videoId}
                  className="rounded-lg border border-neutral-700 bg-surface px-2 py-2 text-sm text-neutral-200"
                >
                  <option value="top">Top</option>
                  <option value="center">Center</option>
                  <option value="bottom">Bottom</option>
                </select>
              </label>
              <label className="flex flex-col gap-1 text-xs text-neutral-400">
                Color
                <input
                  type="color"
                  value={color}
                  onChange={(e) => setColor(e.target.value)}
                  disabled={!videoId}
                  className="h-10 w-full cursor-pointer rounded border border-neutral-700 bg-surface"
                />
              </label>
            </div>
            <label className="mt-3 flex flex-col gap-1 text-xs text-neutral-400">
              Font size: {fontSize}px
              <input
                type="range"
                min={16}
                max={120}
                value={fontSize}
                onChange={(e) => setFontSize(Number(e.target.value))}
                disabled={!videoId}
                className="accent-accent"
              />
            </label>
            <div className="mt-3 grid grid-cols-2 gap-3">
              <label className="flex flex-col gap-1 text-xs text-neutral-400">
                Overlay start (s)
                <input
                  type="number"
                  min={0}
                  step={0.1}
                  value={overlayStart}
                  onChange={(e) => setOverlayStart(Number(e.target.value))}
                  disabled={!videoId}
                  className="rounded-lg border border-neutral-700 bg-surface px-2 py-2 text-sm"
                />
              </label>
              <label className="flex flex-col gap-1 text-xs text-neutral-400">
                Overlay end (s)
                <input
                  type="number"
                  min={0}
                  step={0.1}
                  value={overlayEnd}
                  onChange={(e) => setOverlayEnd(Number(e.target.value))}
                  disabled={!videoId}
                  className="rounded-lg border border-neutral-700 bg-surface px-2 py-2 text-sm"
                />
              </label>
            </div>
          </div>

          <div>
            <h3 className="text-sm font-medium text-neutral-300">Trim</h3>
            <p className="mt-1 text-xs text-neutral-500">
              Range is limited by video duration ({duration.toFixed(1)}s).
            </p>
            <label className="mt-3 flex flex-col gap-1 text-xs text-neutral-400">
              Start: {trimStart.toFixed(1)}s
              <input
                type="range"
                min={0}
                max={maxDur}
                step={0.1}
                value={Math.min(trimStart, maxDur)}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setTrimStart(v);
                  if (v >= trimEnd) setTrimEnd(Math.min(maxDur, v + 0.1));
                }}
                disabled={!videoId}
                className="accent-accent"
              />
            </label>
            <label className="mt-3 flex flex-col gap-1 text-xs text-neutral-400">
              End: {trimEnd.toFixed(1)}s
              <input
                type="range"
                min={0}
                max={maxDur}
                step={0.1}
                value={Math.min(trimEnd, maxDur)}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setTrimEnd(v);
                  if (v <= trimStart) setTrimStart(Math.max(0, v - 0.1));
                }}
                disabled={!videoId}
                className="accent-accent"
              />
            </label>
          </div>

          <div className="flex flex-wrap gap-3">
            <button
              type="button"
              onClick={onApplyEdits}
              disabled={!videoId || editLoading}
              className="rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
            >
              {editLoading ? "Applying…" : "Apply edits"}
            </button>
            <button
              type="button"
              onClick={onSkipEdits}
              disabled={!videoId}
              className="rounded-lg border border-neutral-600 px-4 py-2 text-sm text-neutral-200 hover:bg-neutral-900 disabled:opacity-40"
            >
              Skip edits
            </button>
          </div>
        </div>
        {editError && (
          <p className="mt-4 text-sm text-accent">{editError}</p>
        )}
      </section>

      <section
        className={`rounded-xl border border-neutral-800 bg-neutral-950/80 p-6 shadow-lg ${!activeVideoId ? "opacity-50" : ""
          }`}
      >
        <h2 className="text-lg font-medium text-success">Step 3 — Upload</h2>
        <label className="mt-4 flex flex-col gap-1 text-xs text-neutral-400">
          Title
          <input
            type="text"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            disabled={!activeVideoId}
            className="rounded-lg border border-neutral-700 bg-surface px-3 py-2 text-sm outline-none focus:border-accent focus:ring-1 focus:ring-accent disabled:opacity-50"
          />
        </label>
        <label className="mt-4 flex flex-col gap-1 text-xs text-neutral-400">
          Description
          <textarea
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            disabled={!activeVideoId}
            rows={3}
            className="rounded-lg border border-neutral-700 bg-surface px-3 py-2 text-sm outline-none focus:border-accent focus:ring-1 focus:ring-accent disabled:opacity-50"
          />
        </label>
        <label className="mt-4 flex flex-col gap-1 text-xs text-neutral-400">
          Tags (comma-separated)
          <input
            type="text"
            value={tagsRaw}
            onChange={(e) => setTagsRaw(e.target.value)}
            disabled={!activeVideoId}
            placeholder="shorts, reel, instagram"
            className="rounded-lg border border-neutral-700 bg-surface px-3 py-2 text-sm outline-none focus:border-accent focus:ring-1 focus:ring-accent disabled:opacity-50"
          />
        </label>
        <label className="mt-4 flex flex-col gap-1 text-xs text-neutral-400">
          Privacy
          <select
            value={privacy}
            onChange={(e) => setPrivacy(e.target.value as Privacy)}
            disabled={!activeVideoId}
            className="rounded-lg border border-neutral-700 bg-surface px-2 py-2 text-sm text-neutral-200 disabled:opacity-50"
          >
            <option value="public">Public</option>
            <option value="unlisted">Unlisted</option>
            <option value="private">Private</option>
          </select>
        </label>
        <button
          type="button"
          onClick={onUpload}
          disabled={!activeVideoId || uploadLoading}
          className="mt-4 rounded-lg bg-accent px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
        >
          {uploadLoading ? "Uploading…" : "Upload to YouTube"}
        </button>
        {uploadLoading && (
          <div className="mt-4">
            <div className="h-2 w-full overflow-hidden rounded-full bg-neutral-800">
              <div
                className="h-full bg-success transition-all duration-300"
                style={{ width: `${uploadProgress}%` }}
              />
            </div>
            <p className="mt-2 text-xs text-neutral-500">
              Server is uploading to YouTube; progress is approximate.
            </p>
          </div>
        )}
        {youtubeUrl && (
          <p className="mt-4 text-sm">
            <span className="text-neutral-400">Live: </span>
            <a
              href={youtubeUrl}
              target="_blank"
              rel="noreferrer"
              className="text-success underline hover:text-success/90"
            >
              {youtubeUrl}
            </a>
          </p>
        )}
        {uploadError && (
          <p className="mt-4 text-sm text-accent">{uploadError}</p>
        )}
      </section>
    </main>
  );
}
