"""Analytics router — pull YouTube video stats for uploaded history items."""
import logging

from fastapi import APIRouter, HTTPException

from db import get_history_by_id
from utils import get_youtube_service

router = APIRouter(prefix="/analytics")
logger = logging.getLogger(__name__)


def _extract_video_id(youtube_url: str) -> str | None:
    """Extract a YouTube video ID from a watch URL."""
    import re
    match = re.search(r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})", youtube_url)
    return match.group(1) if match else None


@router.get("/{history_id}")
def get_analytics(history_id: int):
    """Fetch YouTube stats for an uploaded history item."""
    row = get_history_by_id(history_id)
    if not row:
        raise HTTPException(status_code=404, detail="History entry not found.")
    if not row.get("youtube_url"):
        raise HTTPException(status_code=404, detail="No YouTube URL associated with this entry.")

    vid_id = _extract_video_id(row["youtube_url"])
    if not vid_id:
        raise HTTPException(status_code=422, detail="Could not parse YouTube video ID from URL.")

    try:
        youtube = get_youtube_service(row.get("youtube_account", "default"))
        resp = (
            youtube.videos()
            .list(part="statistics,snippet", id=vid_id)
            .execute()
        )
    except Exception as exc:
        logger.error("Analytics fetch failed for history %s: %s", history_id, exc)
        raise HTTPException(status_code=502, detail=f"YouTube API error: {exc}") from exc

    items = resp.get("items", [])
    if not items:
        raise HTTPException(status_code=404, detail="Video not found on YouTube.")

    item = items[0]
    stats = item.get("statistics", {})
    snippet = item.get("snippet", {})

    return {
        "title": snippet.get("title", ""),
        "views": int(stats.get("viewCount", 0)),
        "likes": int(stats.get("likeCount", 0)),
        "comments": int(stats.get("commentCount", 0)),
        "published_at": snippet.get("publishedAt", ""),
    }
