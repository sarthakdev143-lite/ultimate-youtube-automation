"""Download router — supports 7 platforms via yt-dlp."""
import base64
import uuid

import yt_dlp
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from utils import (
    TMP_DIR, COOKIES_PATH, PLATFORM_LABELS,
    cleanup_old_tmp_files, detect_platform, needs_cookies, run_ffmpeg,
)

router = APIRouter()

SUPPORTED_PLATFORMS = ", ".join(PLATFORM_LABELS.values())


class DownloadBody(BaseModel):
    url: str = Field(..., min_length=1)


@router.post("/download")
def download_video(body: DownloadBody):
    """Download from Instagram, Snapchat, TikTok, YouTube Shorts, Twitter/X, Reddit, or Pinterest."""
    cleanup_old_tmp_files()
    url = body.url.strip()

    platform = detect_platform(url)
    if platform is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported URL. Supported platforms: {SUPPORTED_PLATFORMS}.",
        )

    if needs_cookies(platform) and (not COOKIES_PATH.is_file() or COOKIES_PATH.stat().st_size < 50):
        raise HTTPException(
            status_code=400,
            detail="Instagram cookies missing. Export Netscape-format cookies to backend/cookies.txt.",
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
    if needs_cookies(platform):
        ydl_opts["cookiefile"] = str(COOKIES_PATH)

    info = None
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc)
        if any(k in msg.lower() for k in ("private", "login", "cookie", "unavailable")):
            detail = "Download failed: content is private/unavailable, or login is required."
        else:
            detail = f"Download failed: {msg}"
        raise HTTPException(status_code=400, detail=detail) from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Download failed: {exc}") from exc

    if not info:
        raise HTTPException(status_code=500, detail="No metadata returned after download.")

    duration = float(info.get("duration") or 0)
    ext = (info.get("ext") or "mp4").split(".")[-1]
    video_path = TMP_DIR / f"{video_id}.{ext}"

    if not video_path.is_file():
        matches = sorted(
            [p for p in TMP_DIR.glob(f"{video_id}.*") if p.suffix.lower() not in (".jpg", ".jpeg", ".png")],
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not matches:
            raise HTTPException(status_code=500, detail="Downloaded file not found on disk.")
        video_path = matches[0]

    # Generate thumbnail at 1 second
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
        "platform_label": PLATFORM_LABELS.get(platform, platform),
        "title": info.get("title") or "",
        "uploader": info.get("uploader") or "",
    }
