"""Batch pipeline router — Download→Upload in a background task."""
from datetime import datetime, timedelta
import json
import logging
import mimetypes
import uuid
from typing import Literal

import yt_dlp
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from db import insert_history, update_history_status
from utils import (
    TMP_DIR, COOKIES_PATH, PLATFORM_LABELS,
    cleanup_old_tmp_files, detect_platform, needs_cookies,
    check_quota, increment_quota, get_youtube_service, run_ffmpeg,
)

router = APIRouter(prefix="/batch")
logger = logging.getLogger(__name__)

# In-memory store for batch status. Survives until process restart.
_batches: dict[str, list[dict]] = {}


class BatchRunBody(BaseModel):
    urls: list[str]
    privacy: Literal["public", "unlisted", "private"] = "private"
    youtube_account: str = "default"
    schedule_offset_minutes: int = 0


def _run_pipeline(batch_id: str, items: list[dict], body: BatchRunBody) -> None:
    """Background task: download then upload each item sequentially."""
    from googleapiclient.http import MediaFileUpload

    for idx, item in enumerate(items):
        url = item["url"]
        video_id = item["video_id"]
        history_id = item["history_id"]

        # Calculate when this item should run
        run_at = datetime.utcnow() + timedelta(minutes=idx * body.schedule_offset_minutes)

        if body.schedule_offset_minutes > 0 and idx > 0:
            # WARNING: Stagger values >5 minutes will hold a threadpool worker.
            # For production use, set schedule_offset_minutes <= 5 or use the
            # scheduled upload feature instead.
            import time as _time
            wait_sec = (run_at - datetime.utcnow()).total_seconds()
            if wait_sec > 0:
                _time.sleep(wait_sec)

        # Download
        out_template = str(TMP_DIR / f"{video_id}.%(ext)s")
        ydl_opts: dict = {
            "quiet": True,
            "no_warnings": True,
            "outtmpl": out_template,
            "merge_output_format": "mp4",
            "format": "bestvideo+bestaudio/best",
        }
        platform = detect_platform(url)
        if platform and needs_cookies(platform):
            ydl_opts["cookiefile"] = str(COOKIES_PATH)
        if platform == "tiktok":
            ydl_opts["format"] = "bestvideo[vcodec^=h264]+bestaudio/best"
            ydl_opts["extractor_args"] = {"tiktok": {"webpage_download": ["True"]}}

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
        except Exception as exc:
            logger.error("Batch %s item %s download failed: %s", batch_id, video_id, exc)
            update_history_status(history_id, "error_download_failed")
            item["status"] = "error_download_failed"
            continue

        if not info:
            update_history_status(history_id, "error_download_failed")
            item["status"] = "error_download_failed"
            continue

        ext = (info.get("ext") or "mp4").split(".")[-1]
        video_path = TMP_DIR / f"{video_id}.{ext}"
        if not video_path.is_file():
            matches = sorted(
                [p for p in TMP_DIR.glob(f"{video_id}.*") if p.suffix.lower() not in (".jpg", ".jpeg", ".png")],
                key=lambda p: p.stat().st_mtime, reverse=True,
            )
            if not matches:
                update_history_status(history_id, "error_download_failed")
                item["status"] = "error_download_failed"
                continue
            video_path = matches[0]

        title = (info.get("title") or "Untitled")[:100]
        description = (info.get("description") or "")[:5000]
        tags = (info.get("tags") or [])[:30]
        item_platform = platform or ""

        # Upload
        try:
            check_quota(body.youtube_account, 1600)
            youtube = get_youtube_service(body.youtube_account)
            request_body = {
                "snippet": {
                    "title": title,
                    "description": description,
                    "tags": tags,
                    "categoryId": "22",
                },
                "status": {"privacyStatus": body.privacy},
            }
            mime, _ = mimetypes.guess_type(str(video_path))
            media = MediaFileUpload(
                str(video_path), chunksize=-1, resumable=True,
                mimetype=mime or "application/octet-stream"
            )
            insert_resp = (
                youtube.videos()
                .insert(part="snippet,status", body=request_body, media_body=media)
                .execute()
            )
            vid = insert_resp.get("id")
            yt_url = f"https://www.youtube.com/watch?v={vid}" if vid else None
            increment_quota(body.youtube_account, 1600)
            update_history_status(history_id, "uploaded", yt_url)
            item["status"] = "uploaded"
            item["youtube_url"] = yt_url
            logger.info("Batch %s item %s uploaded: %s", batch_id, video_id, yt_url)
        except Exception as exc:
            logger.error("Batch %s item %s upload failed: %s", batch_id, video_id, exc)
            update_history_status(history_id, "error_upload_failed")
            item["status"] = "error_upload_failed"


@router.post("/run")
def run_batch(body: BatchRunBody, background_tasks: BackgroundTasks):
    """Start a batch download+upload pipeline."""
    if not body.urls:
        raise HTTPException(status_code=400, detail="No URLs provided.")
    if body.schedule_offset_minutes > 60:
        raise HTTPException(
            status_code=400,
            detail="schedule_offset_minutes cannot exceed 60. For longer delays, use the scheduled upload feature."
        )

    TMP_DIR.mkdir(parents=True, exist_ok=True)
    cleanup_old_tmp_files()

    batch_id = str(uuid.uuid4())
    items: list[dict] = []

    for url in body.urls:
        url = url.strip()
        if not url:
            continue
        platform = detect_platform(url)
        if platform is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported URL: {url}. Supported: {', '.join(PLATFORM_LABELS.values())}",
            )
        video_id = str(uuid.uuid4())
        history_id = insert_history(
            video_id=video_id,
            source_url=url,
            platform=platform or "",
            status="batch_queued",
            youtube_account=body.youtube_account,
            privacy=body.privacy,
        )
        items.append({
            "url": url,
            "video_id": video_id,
            "history_id": history_id,
            "status": "batch_queued",
            "youtube_url": None,
        })

    _batches[batch_id] = items
    background_tasks.add_task(_run_pipeline, batch_id, items, body)

    return {
        "batch_id": batch_id,
        "items": [{"url": i["url"], "video_id": i["video_id"], "status": i["status"]} for i in items],
    }


@router.get("/{batch_id}/status")
def get_batch_status(batch_id: str):
    """Get current status of all items in a batch."""
    if batch_id not in _batches:
        raise HTTPException(status_code=404, detail="Batch not found.")
    items = _batches[batch_id]
    return {
        "batch_id": batch_id,
        "items": [
            {
                "url": i["url"],
                "video_id": i["video_id"],
                "status": i["status"],
                "youtube_url": i.get("youtube_url"),
            }
            for i in items
        ],
    }
