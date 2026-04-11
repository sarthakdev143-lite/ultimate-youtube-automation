# YT Automation Factory

Web app: paste an Instagram Reel URL, optionally add a text overlay or trim, then upload to YouTube.

- **Frontend:** Next.js (App Router), TypeScript, Tailwind CSS — `frontend/`
- **Backend:** FastAPI — `backend/`

## Prerequisites

- **Python 3.10+**
- **Node.js 18+**
- **ffmpeg** installed and available on your `PATH` (required for thumbnails and edits)

### Installing ffmpeg

- **Windows:** [ffmpeg download](https://ffmpeg.org/download.html) or `winget install ffmpeg`, then confirm `ffmpeg -version` in a new terminal.
- **macOS:** `brew install ffmpeg`
- **Linux:** `sudo apt install ffmpeg` (Debian/Ubuntu) or your distro equivalent.

## Instagram cookies (`backend/cookies.txt`)

1. Install **EditThisCookie**, **Cookie-Editor**, or a similar extension.
2. Log into **Instagram** in your browser.
3. Export cookies in **Netscape** format.
4. Save the file as `backend/cookies.txt` (you can start from `backend/cookies.example.txt` and replace contents).

Without valid cookies, downloads often fail for reels that require a logged-in session.

## YouTube OAuth (`backend/client_secrets.json`)

1. Open [Google Cloud Console](https://console.cloud.google.com).
2. Create a project (or pick an existing one).
3. Enable **YouTube Data API v3**.
4. **Credentials** → **Create credentials** → **OAuth client ID** → **Desktop app**.
5. Download the JSON and save it as `backend/client_secrets.json` (replace the template values).

The first upload triggers a browser sign-in; a refresh token is stored in `backend/token.json` (gitignored). Do not commit real secrets or cookies.

## Run locally

**Backend** (port **8000**):

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```

On macOS/Linux use `source .venv/bin/activate` instead of `.venv\Scripts\activate`.

**Frontend** (port **3000**):

```bash
cd frontend
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). The UI calls the API at `http://localhost:8000`. Override with `NEXT_PUBLIC_API_URL` if needed.

## CORS

The API allows `http://localhost:3000` via FastAPI `CORSMiddleware`.

## API (summary)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/download` | Download reel; returns `video_id`, `duration`, base64 `thumbnail` |
| `POST` | `/edit` | Trim and/or `drawtext` overlay via ffmpeg |
| `POST` | `/upload` | Resumable upload to YouTube |
| `GET` | `/video/{video_id}/thumbnail` | JPEG thumbnail |

Temp files live under `backend/tmp/` and are deleted when older than one hour.

## Error handling

The UI surfaces API error messages for invalid URLs, download failures (cookies, private media), ffmpeg errors, missing OAuth config, quota issues, and upload failures.

## Constraints

- No Docker, no database — UUID filenames under `backend/tmp/` only.
- Respect Instagram and YouTube terms of service; this tool is for content you own or are allowed to republish.
