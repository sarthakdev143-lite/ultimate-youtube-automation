# YT Automation Factory

A fully automated, multi-platform video pipeline. Download short-form content from 7 platforms, apply advanced FFmpeg editing, generate AI-optimized YouTube metadata, and batch-upload or schedule to multiple YouTube channels — all from a single web UI.

- **Frontend:** Next.js 16 (App Router), TypeScript, Tailwind CSS, Puter.js (free AI) — `frontend/`
- **Backend:** FastAPI, `yt-dlp`, FFmpeg, APScheduler, SQLite — `backend/`

---

## Features

### 📥 Multi-Platform Downloads
Download from **Instagram Reels, TikTok, Snapchat Spotlight, YouTube Shorts, Twitter/X, Reddit,** and **Pinterest** via `yt-dlp`.

- **TikTok Watermark Removal** — automatically crops the bottom 80px watermark bar after download.

### 🎬 Advanced Video Editing (FFmpeg)
- Trim start/end, speed (0.25×–4×), rotate, flip H/V
- Color grading: brightness, contrast, saturation
- Fade in/out, mute audio, remove silence
- 9:16 crop for Shorts/Reels, auto-resize to 1080×1920
- Corner watermark text, custom text overlays (any position, size, color, duration)
- Background music injection

### ✨ AI-Powered Metadata
Uses **Puter.js** (GPT-4o, free, no API key) to generate title, description, and tags from video context, transcript, and original metadata.

### 🤖 Telegram Bot Interface
An async Telegram bot can run alongside FastAPI and act as a mobile-first control layer:
- Send a supported reel/short URL to trigger download + metadata generation
- Edit pending metadata with `/title`, `/desc`, `/tags`, `/privacy`
- Upload with `/upload`, cancel with `/cancel`
- Check quota with `/quota`, recent uploads with `/history`, and metrics with `/stats <history_id>`

### 🚀 Batch Pipeline (Auto & Manual)
- **Manual mode:** Add URLs to a queue, download them one-by-one
- **⚡ Auto Pipeline mode:** Paste URLs → select privacy, account, stagger delay → click "Run Pipeline" — all videos download, upload, and stagger automatically in the background. Live status polling (5 s interval) shows YouTube links as they complete.

### 📅 Scheduled Uploads
Schedule any upload for a specific date/time. The APScheduler background job fires every minute and uploads when the time arrives, preserving the full title/description/tags/privacy/webhook set at scheduling time.

### 📋 Pipeline Presets
Save a complete pipeline configuration (all edit settings + upload settings: privacy, title template, description, tags, account, webhook URL) as a named preset. Load it in one click to instantly repopulate all fields.

### 🔑 Headless OAuth (works on hosted backends)
Add YouTube accounts without needing a browser on the server. The UI opens Google's auth page in a new tab, you paste the code back, and the token is saved — fully compatible with Render/Railway deployments.

### 🔔 Upload Completion Webhooks
Provide a webhook URL in Step 3 (or in a pipeline preset). On every successful upload (immediate or scheduled), the backend POSTs:
```json
{
  "event": "upload_complete",
  "youtube_url": "https://www.youtube.com/watch?v=...",
  "title": "...",
  "platform": "tiktok",
  "video_id": "...",
  "timestamp": "2026-04-12T07:00:00Z"
}
```
Works with Discord webhooks, Make.com, Zapier, or any HTTP endpoint.

### 🖼 Custom Thumbnail
Toggle "Use custom thumbnail" in Step 3, choose which second of the video to extract as a 1280×720 JPEG, and preview it before uploading. The frame is set on YouTube via `thumbnails.set()` after the video upload completes.

### 📊 YouTube Analytics
In the History page, click "📊 Stats" on any uploaded row to fetch live view, like, and comment counts from the YouTube Data API. Results are cached per session.

### 📆 Schedule Calendar View
`/schedule` page shows a 7-day calendar grid with all scheduled and uploaded items colour-coded:
- 🟡 Amber — scheduled
- 🟢 Green — uploaded
- 🔴 Red — error

Click any card to see full details and the YouTube link.

### 🛡 Quota Guard
The backend tracks daily YouTube API quota usage per account in `backend/quota.json`. Each upload costs 1,600 units out of the 10,000 daily limit. Attempts that would exceed the quota are rejected with HTTP 429 before the upload is even started.

The Studio UI shows a live quota indicator (green / amber / red) below the account selector.

### 🌙 Theme Persistence (no flash)
A blocking inline script in `layout.tsx` reads `localStorage.theme` and sets `data-theme` on `<html>` before React hydrates — eliminating the flash of unstyled content.

---

## Prerequisites

- **Python 3.10+**
- **Node.js 18+**
- **ffmpeg** on your `PATH`

### Installing ffmpeg

| OS | Command |
|---|---|
| Windows | `winget install ffmpeg` or [download](https://ffmpeg.org/download.html) |
| macOS | `brew install ffmpeg` |
| Linux | `sudo apt install ffmpeg` |

Verify: `ffmpeg -version`

---

## Credentials Setup

### 1. Instagram Cookies — `backend/cookies.txt`
Required only for private/restricted Instagram content.
1. Install **Cookie-Editor** browser extension.
2. Log into Instagram.
3. Export cookies in **Netscape** format.
4. Save as `backend/cookies.txt`.

### 2. YouTube OAuth — `backend/client_secrets.json`
Required to upload to YouTube.
1. [Google Cloud Console](https://console.cloud.google.com) → **Enable YouTube Data API v3**.
2. **Credentials → Create → OAuth client ID → Desktop app**.
3. Download the JSON → save as `backend/client_secrets.json`.

**Adding accounts (hosted backend):** Click "+ Add Account" in the Studio. A Google sign-in page opens in a new tab. Paste the code back into the UI — no browser on the server needed.

---

## Run Locally

**Backend** (port **8000**):
```bash
cd backend
python -m venv venv
venv\Scripts\activate        # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

### Telegram Bot Setup

1. Create a Telegram bot via [@BotFather](https://t.me/BotFather) and copy the bot token.
2. Get your chat ID from [@userinfobot](https://t.me/userinfobot).
3. Get a Groq API key from [console.groq.com](https://console.groq.com).
4. Add these values to `backend/.env`:
   ```env
   TELEGRAM_BOT_TOKEN=...
   TELEGRAM_ALLOWED_CHAT_IDS=123456789
   TELEGRAM_DEFAULT_ACCOUNT=default
   TELEGRAM_DEFAULT_PRIVACY=private
   TELEGRAM_WATERMARK=@YourChannel
   GROQ_API_KEY=...
   ```
5. Restart the backend. The bot starts automatically during FastAPI startup.
6. Send any supported URL to your bot to begin.

**Frontend** (port **3000**):
```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The UI calls the API at `http://localhost:8000`. Override with `NEXT_PUBLIC_API_URL` in `frontend/.env.local`.

---

## Deployment

### Frontend → Vercel
Connect your repo. Set environment variable:
```
NEXT_PUBLIC_API_URL=https://your-backend.onrender.com
```
`vercel.json` is pre-configured for Next.js.

### Backend → Render / Railway
A `render.yaml` is included. For Render free tier:
- **Build:** `cd backend && pip install -r requirements.txt`
- **Start:** `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT`

Ensure `ffmpeg` is available in the build environment (Render provides it on Ubuntu images).

> **Note:** `factory.db`, `token_*.json`, `quota.json`, and `cookies.txt` are in `.gitignore` and must not be committed. The DB schema auto-migrates on startup.

---

## API Reference

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/download` | Download video via yt-dlp (7 platforms) |
| `POST` | `/edit` | Apply FFmpeg edits to a downloaded video |
| `POST` | `/upload` | Upload to YouTube (immediate or scheduled) |
| `GET` | `/youtube/accounts` | List authenticated accounts |
| `GET` | `/youtube/accounts/auth-url?account=` | Get headless OAuth URL |
| `POST` | `/youtube/accounts/exchange` | Exchange OAuth code → save token |
| `GET` | `/quota/{account}` | Get today's API quota usage |
| `POST` | `/batch/run` | Start auto-pipeline batch |
| `GET` | `/batch/{batch_id}/status` | Poll batch item statuses |
| `GET` | `/analytics/{history_id}` | Fetch YouTube stats for an upload |
| `GET` | `/history` | List upload history |
| `GET` | `/presets` | List edit presets |
| `POST` | `/presets` | Save edit preset |
| `GET` | `/presets/pipeline` | List pipeline presets |
| `POST` | `/presets/pipeline` | Save pipeline preset |
| `DELETE` | `/presets/pipeline/{id}` | Delete pipeline preset |
| `POST` | `/ai/generate-subtitles` | Transcribe video with Whisper |
| `GET` | `/stats/disk` | Temp storage stats |
| `GET` | `/video/{video_id}/thumbnail` | JPEG thumbnail |
| `GET` | `/video/{video_id}/file` | Stream video file |

Temp files live in `backend/tmp/` and auto-purge after 1 hour.

---

## Constraints & Notices

- Respect platform terms of service — only automate content you own or have the right to republish.
- YouTube Data API v3 free quota is **10,000 units/day**. Each video upload costs **1,600 units** (~6 uploads/day). The built-in quota guard prevents accidental overages.
- The Whisper transcription endpoint enforces a **200 MB** file size limit to prevent server crashes.
- Out-of-band (OOB) OAuth requires `client_secrets.json` to be configured as a **Desktop app** type.
