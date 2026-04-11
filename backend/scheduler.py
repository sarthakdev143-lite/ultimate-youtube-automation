"""APScheduler background job: fires pending scheduled uploads."""
import logging
import mimetypes

from apscheduler.schedulers.background import BackgroundScheduler

from db import get_scheduled_pending, update_history_status
from utils import find_video_path, get_youtube_service

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _process_scheduled_uploads() -> None:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    pending = get_scheduled_pending()
    for row in pending:
        history_id: int = row["id"]
        video_id: str = row["video_id"]
        title: str = row["title"] or "Untitled"

        # Mark as processing to avoid double-firing
        update_history_status(history_id, "processing")

        try:
            path = find_video_path(video_id)
        except Exception:
            update_history_status(history_id, "error_file_missing")
            logger.warning("Scheduled upload %s: video file missing", history_id)
            continue

        try:
            youtube = get_youtube_service()
            request_body = {
                "snippet": {
                    "title": title[:100],
                    "description": "",
                    "tags": [],
                    "categoryId": "22",
                },
                "status": {"privacyStatus": "public"},
            }
            mime, _ = mimetypes.guess_type(str(path))
            media = MediaFileUpload(
                str(path),
                chunksize=-1,
                resumable=True,
                mimetype=mime or "application/octet-stream",
            )
            insert = (
                youtube.videos()
                .insert(part="snippet,status", body=request_body, media_body=media)
                .execute()
            )
            vid = insert.get("id")
            yt_url = f"https://www.youtube.com/watch?v={vid}" if vid else None
            update_history_status(history_id, "uploaded", yt_url)
            logger.info("Scheduled upload done: history=%s yt=%s", history_id, yt_url)
        except Exception as exc:
            update_history_status(history_id, "error_upload_failed")
            logger.error("Scheduled upload failed history=%s: %s", history_id, exc)


def start_scheduler() -> None:
    global _scheduler
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _process_scheduled_uploads,
        trigger="interval",
        minutes=1,
        id="scheduled_uploads",
        max_instances=1,
    )
    _scheduler.start()
    logger.info("APScheduler started — checking for scheduled uploads every 60s.")


def stop_scheduler() -> None:
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped.")
