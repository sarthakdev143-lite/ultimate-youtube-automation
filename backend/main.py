import base64
import json
import mimetypes
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from pydantic import BaseModel, Field

import yt_dlp

BACKEND_DIR = Path(__file__).resolve().parent
TMP_DIR = BACKEND_DIR / "tmp"
COOKIES_PATH = BACKEND_DIR / "cookies.txt"
CLIENT_SECRETS_PATH = BACKEND_DIR / "client_secrets.json"
TOKEN_PATH = BACKEND_DIR / "token.json"

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

INSTAGRAM_HOST_RE = re.compile(
    r"^https?://(?:www\.)?instagram\.com/(?:reel|reels|p|tv)/[\w-]+",
    re.IGNORECASE,
)

app = FastAPI(title="YT Automation Factory API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def cleanup_old_tmp_files(max_age_sec: int = 3600) -> None:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for path in TMP_DIR.iterdir():
        if not path.is_file():
            continue
        try:
            if now - path.stat().st_mtime > max_age_sec:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def find_video_path(video_id: str) -> Path:
    matches = list(TMP_DIR.glob(f"{video_id}.*"))
    matches = [p for p in matches if p.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp")]
    if not matches:
        raise HTTPException(status_code=404, detail="Video not found or expired. Download again.")
    return matches[0]


def run_ffmpeg(args: list[str]) -> None:
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", *args],
            capture_output=True,
            text=True,
            timeout=7200,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail="ffmpeg is not installed or not on PATH. Install ffmpeg and retry.",
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Video processing timed out.")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[-2000:]
        raise HTTPException(status_code=500, detail=f"ffmpeg failed: {err or 'unknown error'}")


def escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
    )


def hex_to_ffmpeg_color(color: str) -> str:
    c = color.strip().lstrip("#")
    if len(c) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in c):
        return f"0x{c}FF"
    if len(c) == 8 and all(ch in "0123456789abcdefABCDEF" for ch in c):
        return f"0x{c}"
    raise HTTPException(status_code=400, detail="Invalid color. Use #RRGGBB or #RRGGBBAA.")


def get_youtube_service():
    if not CLIENT_SECRETS_PATH.is_file():
        raise HTTPException(
            status_code=503,
            detail="YouTube OAuth is not configured. Add client_secrets.json (see README).",
        )
    creds = None
    if TOKEN_PATH.is_file():
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), YOUTUBE_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"Could not refresh YouTube token. Re-authenticate: {exc}",
                ) from exc
        else:
            try:
                flow = InstalledAppFlow.from_client_secrets_file(
                    str(CLIENT_SECRETS_PATH), YOUTUBE_SCOPES
                )
                creds = flow.run_local_server(port=0)
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail=f"OAuth setup failed. Check client_secrets.json: {exc}",
                ) from exc
        TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


class DownloadBody(BaseModel):
    url: str = Field(..., min_length=1)


class TextOverlay(BaseModel):
    text: str
    position: str
    font_size: int = Field(ge=8, le=200)
    color: str
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)


class TrimSpec(BaseModel):
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)


class EditBody(BaseModel):
    video_id: str
    text_overlay: TextOverlay | None = None
    trim: TrimSpec | None = None


class UploadBody(BaseModel):
    video_id: str
    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    privacy: str


@app.post("/download")
def download_reel(body: DownloadBody):
    cleanup_old_tmp_files()
    url = body.url.strip()
    if not INSTAGRAM_HOST_RE.match(url):
        raise HTTPException(
            status_code=400,
            detail="Invalid or unsupported Instagram URL. Use a reel or post URL from instagram.com.",
        )
    if not COOKIES_PATH.is_file() or COOKIES_PATH.stat().st_size < 50:
        raise HTTPException(
            status_code=400,
            detail="Instagram cookies missing or empty. Export Netscape cookies to backend/cookies.txt.",
        )

    video_id = str(uuid.uuid4())
    out_template = str(TMP_DIR / f"{video_id}.%(ext)s")

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "cookiefile": str(COOKIES_PATH),
        "outtmpl": out_template,
        "merge_output_format": "mp4",
        "format": "bestvideo+bestaudio/best",
    }

    info = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc)
        if "private" in msg.lower() or "login" in msg.lower() or "cookie" in msg.lower():
            detail = "Download failed: private video, login required, or cookies expired. Refresh cookies.txt."
        else:
            detail = f"Download failed: {msg}"
        raise HTTPException(status_code=400, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Download failed: {exc}") from exc

    if not info:
        raise HTTPException(status_code=500, detail="Download finished but no metadata was returned.")

    duration = float(info.get("duration") or 0)
    ext = (info.get("ext") or "mp4").split(".")[-1]
    video_path = TMP_DIR / f"{video_id}.{ext}"
    if not video_path.is_file():
        matches = sorted(TMP_DIR.glob(f"{video_id}.*"), key=lambda p: p.stat().st_mtime, reverse=True)
        matches = [p for p in matches if p.suffix.lower() not in (".jpg", ".jpeg", ".png")]
        if matches:
            video_path = matches[0]
        else:
            raise HTTPException(status_code=500, detail="Downloaded file not found on disk.")

    thumb_path = TMP_DIR / f"{video_id}_thumb.jpg"
    run_ffmpeg(["-ss", "1", "-i", str(video_path), "-frames:v", "1", str(thumb_path)])

    thumb_b64 = ""
    if thumb_path.is_file():
        thumb_b64 = base64.b64encode(thumb_path.read_bytes()).decode("ascii")

    return {
        "video_id": video_id,
        "filename": video_path.name,
        "duration": duration,
        "thumbnail": thumb_b64,
    }


@app.post("/edit")
def edit_video(body: EditBody):
    cleanup_old_tmp_files()
    src = find_video_path(body.video_id)

    trim = body.trim
    overlay = body.text_overlay
    has_overlay = overlay is not None and bool(overlay.text.strip())
    if not trim and not has_overlay:
        raise HTTPException(
            status_code=400,
            detail="Provide a trim range and/or a non-empty text overlay.",
        )
    if trim and trim.end_sec <= trim.start_sec:
        raise HTTPException(status_code=400, detail="Trim end must be greater than trim start.")
    if has_overlay and overlay is not None:
        if overlay.end_sec <= overlay.start_sec:
            raise HTTPException(status_code=400, detail="Text overlay end must be greater than start.")
        if overlay.position not in ("top", "bottom", "center"):
            raise HTTPException(status_code=400, detail='Position must be "top", "bottom", or "center".')

    edited_id = str(uuid.uuid4())
    out_path = TMP_DIR / f"{edited_id}.mp4"

    vf_parts: list[str] = []
    if has_overlay and overlay is not None:
        fc = hex_to_ffmpeg_color(overlay.color)
        pos = overlay.position
        if pos == "top":
            xy = "x=(w-text_w)/2:y=50"
        elif pos == "bottom":
            xy = "x=(w-text_w)/2:y=h-text_h-50"
        else:
            xy = "x=(w-text_w)/2:y=(h-text_h)/2"
        esc = escape_drawtext(overlay.text.strip())
        enable = f"between(t\\,{overlay.start_sec}\\,{overlay.end_sec})"
        vf_parts.append(
            f"drawtext=text='{esc}':fontsize={overlay.font_size}:fontcolor={fc}:{xy}:enable='{enable}'"
        )

    vf_arg = ",".join(vf_parts) if vf_parts else None

    cmd: list[str] = []
    if trim:
        cmd.extend(["-ss", str(trim.start_sec), "-to", str(trim.end_sec)])
    cmd.extend(["-i", str(src)])
    if vf_arg:
        cmd.extend(["-vf", vf_arg])
    cmd.extend(["-c:a", "aac", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_path)])

    run_ffmpeg(cmd)

    if not out_path.is_file():
        raise HTTPException(status_code=500, detail="Edited file was not created.")

    return {"edited_video_id": edited_id}


@app.post("/upload")
def upload_to_youtube(body: UploadBody):
    cleanup_old_tmp_files()
    if body.privacy not in ("public", "unlisted", "private"):
        raise HTTPException(
            status_code=400,
            detail='privacy must be "public", "unlisted", or "private".',
        )
    path = find_video_path(body.video_id)

    status_map = {"public": "public", "unlisted": "unlisted", "private": "private"}

    youtube = get_youtube_service()

    request_body = {
        "snippet": {
            "title": body.title[:100] or "Untitled",
            "description": body.description[:5000],
            "tags": body.tags[:30],
            "categoryId": "22",
        },
        "status": {"privacyStatus": status_map[body.privacy]},
    }

    mime, _ = mimetypes.guess_type(str(path))
    media = MediaFileUpload(
        str(path), chunksize=-1, resumable=True, mimetype=mime or "application/octet-stream"
    )

    try:
        insert = (
            youtube.videos()
            .insert(part="snippet,status", body=request_body, media_body=media)
            .execute()
        )
    except HttpError as exc:
        err = json.loads(exc.content.decode("utf-8")) if exc.content else {}
        reason = (
            (err.get("error") or {}).get("errors") or [{}]
        )[0].get("reason", "")
        message = (err.get("error") or {}).get("message", str(exc))
        if exc.resp.status == 403 and (
            "quota" in message.lower() or reason == "quotaExceeded"
        ):
            raise HTTPException(
                status_code=429,
                detail="YouTube API quota exceeded. Try again tomorrow or request a higher quota.",
            ) from exc
        raise HTTPException(status_code=400, detail=f"YouTube upload failed: {message}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc

    vid = insert.get("id")
    if not vid:
        raise HTTPException(status_code=500, detail="Upload succeeded but no video ID returned.")

    return {"youtube_url": f"https://www.youtube.com/watch?v={vid}"}


@app.get("/video/{video_id}/thumbnail")
def get_thumbnail(video_id: str):
    cleanup_old_tmp_files()
    thumb = TMP_DIR / f"{video_id}_thumb.jpg"
    if not thumb.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found.")
    return FileResponse(thumb, media_type="image/jpeg")


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    detail = exc.detail
    if not isinstance(detail, str):
        detail = str(detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})
