# YT Automation Factory

A fully automated, multi-platform video pipeline. Download short-form content from platforms like Instagram Reels, TikTok, and Snapchat, apply advanced video editing (trim, crop, rotate, color grading, text overlays, background music), generate optimized YouTube metadata using AI, and batch-upload or schedule to multiple YouTube channels.

- **Frontend:** Next.js (App Router), TypeScript, Tailwind CSS, Puter.js (AI Metadata & features) — `frontend/`
- **Backend:** FastAPI, `yt-dlp`, FFmpeg, APScheduler — `backend/`

## Features

- **Multi-Platform Downloads:** Retrieve videos reliably from Instagram, TikTok, Snapchat, and more using `yt-dlp`.
- **Advanced Editing Pipeline:** Seamlessly trim, crop, rotate, apply color grading, add dynamic text overlays, and inject background music via FFmpeg.
- **AI-Powered Metadata:** Leverage free AI models (via Puter.js) to generate highly optimized YouTube titles, descriptions, and tags based on transcriptions or video context.
- **Multi-Account Uploads:** Manage multiple YouTube accounts seamlessly. Select specific channels for different batches of content.
- **Scheduling & Batch Processing:** Configure upload pipelines, define presets, and schedule uploads sequentially to avoid API rate limits.
- **Deployable:** Easily deployable backend and frontend (e.g., to Vercel).

## Prerequisites

- **Python 3.10+**
- **Node.js 18+**
- **ffmpeg** installed and available on your `PATH` (required for all video editing, thumbnails, and audio processing)

### Installing ffmpeg

- **Windows:** [ffmpeg download](https://ffmpeg.org/download.html) or `winget install ffmpeg`, then confirm `ffmpeg -version` in a new terminal.
- **macOS:** `brew install ffmpeg`
- **Linux:** `sudo apt install ffmpeg` (Debian/Ubuntu) or your distro equivalent.

## Platform Setup & Credentials

### 1. Instagram Cookies (`backend/cookies.txt`)
_Required to download private or restricted reels._
1. Install an extension like **Cookie-Editor**.
2. Log into **Instagram**.
3. Export cookies in **Netscape** format.
4. Save the file as `backend/cookies.txt`.

### 2. YouTube OAuth (`backend/client_secrets.json`)
_Required to upload to YouTube channels._
1. Open [Google Cloud Console](https://console.cloud.google.com).
2. Enable **YouTube Data API v3**.
3. **Credentials** → **Create credentials** → **OAuth client ID** → **Desktop app**.
4. Save the downloaded JSON as `backend/client_secrets.json`.

*Note: The system supports multi-account uploads. On your first upload for a selected profile, it triggers a browser sign-in and stores a specific token for that channel.*

## Run locally

**Backend** (port **8000**):

```bash
cd backend
python -m venv .venv
# Activate virtual environment
.venv\Scripts\activate # On macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

**Frontend** (port **3000**):

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The UI calls the API at `http://localhost:8000`. You can override this using `NEXT_PUBLIC_API_URL` in your `.env` file.

## Deployment (Vercel)

The frontend is fully compatible with Vercel deployments. Just connect your repository and configure the `NEXT_PUBLIC_API_URL` environment variable to point to your hosted backend.

For the backend, you can deploy to Render, Railway, or Heroku (a `render.yaml` template is provided), ensuring that `ffmpeg` is available in your production environment (e.g., using `apt-get` in a custom build script or using Docker).

## API Overview

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/download` | Download short form video via `yt-dlp` |
| `POST` | `/edit` | Process video (trim, crop, color grade, text, bg music) |
| `POST` | `/upload` | Resumable upload to a selected YouTube channel |
| `GET`  | `/video/{video_id}/thumbnail` | Retrieve JPEG thumbnail |
| `POST` | `/schedule` | Queue video batch processing and scheduling |

Temp files live under `backend/tmp/` and are actively cleared after processing.

## Constraints & Notices
- Respect platform terms of service; this tool is for automating content you own or have the explicit right to republish.
- No database required by default — relies on local filesystem structures and token management.
