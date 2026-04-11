import base64
import json
import mimetypes
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Literal

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

# Supported platform URL patterns
INSTAGRAM_RE = re.compile(
    r"^https?://(?:www\.)?instagram\.com/(?:reel|reels|p|tv)/[\w-]+",
    re.IGNORECASE,
)
SNAPCHAT_RE = re.compile(
    r"^https?://(?:www\.)?snapchat\.com/spotlight/[\w-]+",
    re.IGNORECASE,
)

Platform = Literal["instagram", "snapchat"]


def detect_platform(url: str) -> Platform | None:
    if INSTAGRAM_RE.match(url):
        return "instagram"
    if SNAPCHAT_RE.match(url):
        return "snapchat"
    return None


app = FastAPI(title="YT Automation Factory API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class DownloadBody(BaseModel):
    url: str = Field(..., min_length=1)


class TextOverlay(BaseModel):
    text: str
    position: str  # "top" | "bottom" | "center" | "top-left" | "top-right" | "bottom-left" | "bottom-right"
    font_size: int = Field(ge=8, le=200)
    color: str
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)


class TrimSpec(BaseModel):
    start_sec: float = Field(ge=0)
    end_sec: float = Field(ge=0)


class ColorGrade(BaseModel):
    """All values: 0.0–3.0, default 1.0 (no change)."""
    brightness: float = Field(default=1.0, ge=0.0, le=3.0)
    contrast: float = Field(default=1.0, ge=0.0, le=3.0)
    saturation: float = Field(default=1.0, ge=0.0, le=3.0)


class EditBody(BaseModel):
    video_id: str
    # Editing features
    text_overlays: list[TextOverlay] = Field(default_factory=list)
    trim: TrimSpec | None = None
    color_grade: ColorGrade | None = None
    speed: float = Field(default=1.0, ge=0.25, le=4.0)
    mute_audio: bool = False
    fade_in_sec: float = Field(default=0.0, ge=0.0)
    fade_out_sec: float = Field(default=0.0, ge=0.0)
    watermark_text: str = ""


class UploadBody(BaseModel):
    video_id: str
    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    privacy: str


# ---------------------------------------------------------------------------
# Position helper
# ---------------------------------------------------------------------------

def overlay_xy(position: str) -> str:
    """Return ffmpeg drawtext x/y expression for a named position."""
    positions = {
        "top":          "x=(w-text_w)/2:y=50",
        "bottom":       "x=(w-text_w)/2:y=h-text_h-50",
        "center":       "x=(w-text_w)/2:y=(h-text_h)/2",
        "top-left":     "x=30:y=30",
        "top-right":    "x=w-text_w-30:y=30",
        "bottom-left":  "x=30:y=h-text_h-30",
        "bottom-right": "x=w-text_w-30:y=h-text_h-30",
    }
    return positions.get(position, "x=(w-text_w)/2:y=h-text_h-50")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/download")
def download_video(body: DownloadBody):
    """Download a video from Instagram Reels or Snapchat Spotlight."""
    cleanup_old_tmp_files()
    url = body.url.strip()

    platform = detect_platform(url)
    if platform is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid or unsupported URL. "
                "Supported: Instagram Reels (instagram.com/reel/…) "
                "and Snapchat Spotlights (snapchat.com/spotlight/…)."
            ),
        )

    # Instagram requires cookies; Snapchat Spotlight is typically public
    use_cookies = platform == "instagram"
    if use_cookies and (not COOKIES_PATH.is_file() or COOKIES_PATH.stat().st_size < 50):
        raise HTTPException(
            status_code=400,
            detail="Instagram cookies missing or empty. Export Netscape cookies to backend/cookies.txt.",
        )

    video_id = str(uuid.uuid4())
    out_template = str(TMP_DIR / f"{video_id}.%(ext)s")
    TMP_DIR.mkdir(parents=True, exist_ok=True)

    ydl_opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "outtmpl": out_template,
        "merge_output_format": "mp4",
        "format": "bestvideo+bestaudio/best",
    }
    if use_cookies:
        ydl_opts["cookiefile"] = str(COOKIES_PATH)

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
        "platform": platform,
    }


@app.post("/edit")
def edit_video(body: EditBody):
    cleanup_old_tmp_files()
    src = find_video_path(body.video_id)

    # Validate trim
    trim = body.trim
    if trim and trim.end_sec <= trim.start_sec:
        raise HTTPException(status_code=400, detail="Trim end must be greater than trim start.")

    # Validate overlays
    valid_positions = {"top", "bottom", "center", "top-left", "top-right", "bottom-left", "bottom-right"}
    for ov in body.text_overlays:
        if not ov.text.strip():
            raise HTTPException(status_code=400, detail="Overlay text cannot be empty.")
        if ov.end_sec <= ov.start_sec:
            raise HTTPException(status_code=400, detail="Text overlay end must be greater than start.")
        if ov.position not in valid_positions:
            raise HTTPException(status_code=400, detail=f"Invalid overlay position: {ov.position}")

    # Require at least one edit operation
    has_overlays = len(body.text_overlays) > 0
    has_trim = trim is not None
    has_color = body.color_grade is not None
    has_speed = abs(body.speed - 1.0) > 0.01
    has_mute = body.mute_audio
    has_fade = body.fade_in_sec > 0 or body.fade_out_sec > 0
    has_watermark = bool(body.watermark_text.strip())

    if not any([has_overlays, has_trim, has_color, has_speed, has_mute, has_fade, has_watermark]):
        raise HTTPException(status_code=400, detail="Provide at least one edit operation.")

    edited_id = str(uuid.uuid4())
    out_path = TMP_DIR / f"{edited_id}.mp4"

    # -----------------------------------------------------------------------
    # Build ffmpeg video filter chain
    # -----------------------------------------------------------------------
    vf_parts: list[str] = []

    # 1. Color grading via `eq` filter
    if has_color and body.color_grade:
        cg = body.color_grade
        vf_parts.append(
            f"eq=brightness={cg.brightness - 1.0:.4f}"
            f":contrast={cg.contrast:.4f}"
            f":saturation={cg.saturation:.4f}"
        )

    # 2. Speed change — video part (setpts)
    if has_speed:
        pts_factor = 1.0 / body.speed
        vf_parts.append(f"setpts={pts_factor:.6f}*PTS")

    # 3. Fade in / out (video)
    if has_fade:
        # We need video duration for fade-out; use a placeholder expression
        if body.fade_in_sec > 0:
            vf_parts.append(f"fade=t=in:st=0:d={body.fade_in_sec:.2f}")
        if body.fade_out_sec > 0:
            # Approximate: calculate start from trim if provided
            fade_out_start = (trim.end_sec - trim.start_sec - body.fade_out_sec) if trim else -1
            if fade_out_start > 0:
                vf_parts.append(f"fade=t=out:st={fade_out_start:.2f}:d={body.fade_out_sec:.2f}")

    # 4. Text overlays
    for ov in body.text_overlays:
        fc = hex_to_ffmpeg_color(ov.color)
        xy = overlay_xy(ov.position)
        esc = escape_drawtext(ov.text.strip())
        enable = f"between(t\\,{ov.start_sec}\\,{ov.end_sec})"
        vf_parts.append(
            f"drawtext=text='{esc}':fontsize={ov.font_size}:fontcolor={fc}"
            f":{xy}:enable='{enable}'"
        )

    # 5. Watermark (persistent bottom-right corner branding)
    if has_watermark:
        esc_wm = escape_drawtext(body.watermark_text.strip())
        vf_parts.append(
            f"drawtext=text='{esc_wm}':fontsize=28:fontcolor=0xFFFFFFAA"
            f":x=w-text_w-20:y=h-text_h-20"
        )

    # -----------------------------------------------------------------------
    # Build ffmpeg audio filter chain
    # -----------------------------------------------------------------------
    af_parts: list[str] = []

    # Speed change — audio part (atempo supports 0.5–2.0; chain for wider range)
    if has_speed:
        speed = body.speed
        if speed < 0.5:
            af_parts += ["atempo=0.5", f"atempo={speed / 0.5:.4f}"]
        elif speed > 2.0:
            af_parts += ["atempo=2.0", f"atempo={speed / 2.0:.4f}"]
        else:
            af_parts.append(f"atempo={speed:.4f}")

    # Audio fade
    if has_fade:
        if body.fade_in_sec > 0:
            af_parts.append(f"afade=t=in:st=0:d={body.fade_in_sec:.2f}")
        if body.fade_out_sec > 0:
            fade_out_start = (trim.end_sec - trim.start_sec - body.fade_out_sec) if trim else -1
            if fade_out_start > 0:
                af_parts.append(f"afade=t=out:st={fade_out_start:.2f}:d={body.fade_out_sec:.2f}")

    # -----------------------------------------------------------------------
    # Assemble final ffmpeg command
    # -----------------------------------------------------------------------
    cmd: list[str] = []
    if trim:
        cmd.extend(["-ss", str(trim.start_sec), "-to", str(trim.end_sec)])
    cmd.extend(["-i", str(src)])

    if has_mute:
        cmd.extend(["-an"])  # strip audio entirely
    else:
        if af_parts:
            cmd.extend(["-af", ",".join(af_parts)])
        cmd.extend(["-c:a", "aac"])

    if vf_parts:
        cmd.extend(["-vf", ",".join(vf_parts)])

    cmd.extend(["-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_path)])

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

    youtube = get_youtube_service()

    request_body = {
        "snippet": {
            "title": body.title[:100] or "Untitled",
            "description": body.description[:5000],
            "tags": body.tags[:30],
            "categoryId": "22",
        },
        "status": {"privacyStatus": body.privacy},
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
