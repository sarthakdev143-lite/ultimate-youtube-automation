"""Upload router — immediate + scheduled uploads, and video streaming."""
import json
import mimetypes
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from pydantic import BaseModel, Field

from db import get_history_by_id, insert_history, update_history_status
from utils import BACKEND_DIR, TMP_DIR, authenticate_youtube_account, cleanup_old_tmp_files, find_video_path, get_youtube_service

router = APIRouter()


class UploadBody(BaseModel):
    video_id: str
    title: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    privacy: str
    source_url: str = ""
    platform: str = ""
    scheduled_at: str | None = None  # ISO 8601, e.g. "2024-12-01T18:00:00"
    youtube_account: str = "default"


@router.post("/upload")
def upload_to_youtube(body: UploadBody):
    cleanup_old_tmp_files()

    if body.privacy not in ("public", "unlisted", "private"):
        raise HTTPException(status_code=400, detail='privacy must be "public", "unlisted", or "private".')

    path = find_video_path(body.video_id)

    # ── Scheduled upload ──────────────────────────────────────────────────
    if body.scheduled_at:
        try:
            datetime.fromisoformat(body.scheduled_at)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid scheduled_at. Use ISO 8601 format.")
        history_id = insert_history(
            video_id=body.video_id,
            source_url=body.source_url,
            platform=body.platform,
            title=body.title or "Untitled",
            status="scheduled",
            scheduled_at=body.scheduled_at,
            youtube_account=body.youtube_account,
        )
        return {"scheduled": True, "history_id": history_id, "scheduled_at": body.scheduled_at}

    # ── Immediate upload ──────────────────────────────────────────────────
    youtube = get_youtube_service(body.youtube_account)
    request_body = {
        "snippet": {
            "title": (body.title or "Untitled")[:100],
            "description": body.description[:5000],
            "tags": body.tags[:30],
            "categoryId": "22",
        },
        "status": {"privacyStatus": body.privacy},
    }
    mime, _ = mimetypes.guess_type(str(path))
    media = MediaFileUpload(str(path), chunksize=-1, resumable=True, mimetype=mime or "application/octet-stream")

    try:
        insert = (
            youtube.videos()
            .insert(part="snippet,status", body=request_body, media_body=media)
            .execute()
        )
    except HttpError as exc:
        err = json.loads(exc.content.decode()) if exc.content else {}
        reason = ((err.get("error") or {}).get("errors") or [{}])[0].get("reason", "")
        message = (err.get("error") or {}).get("message", str(exc))
        if exc.resp.status == 403 and ("quota" in message.lower() or reason == "quotaExceeded"):
            raise HTTPException(status_code=429, detail="YouTube API quota exceeded. Try again tomorrow.") from exc
        raise HTTPException(status_code=400, detail=f"YouTube upload failed: {message}") from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Upload failed: {exc}") from exc

    vid = insert.get("id")
    if not vid:
        raise HTTPException(status_code=500, detail="Upload succeeded but no video ID returned.")

    yt_url = f"https://www.youtube.com/watch?v={vid}"
    insert_history(
        video_id=body.video_id,
        source_url=body.source_url,
        platform=body.platform,
        title=body.title or "Untitled",
        youtube_url=yt_url,
        status="uploaded",
        youtube_account=body.youtube_account,
    )
    return {"youtube_url": yt_url}


class AccountBody(BaseModel):
    account: str

@router.get("/youtube/accounts")
def get_youtube_accounts():
    accounts = set(["default"])
    for p in BACKEND_DIR.glob("token_*.json"):
        name = p.stem.replace("token_", "")
        accounts.add(name)
    return {"accounts": sorted(list(accounts))}

@router.post("/youtube/accounts")
def add_youtube_account(body: AccountBody):
    if not body.account.strip():
        raise HTTPException(status_code=400, detail="Account name is required.")
    try:
        authenticate_youtube_account(body.account.strip())
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=str(e))
    return {"success": True, "account": body.account.strip()}


@router.get("/upload/status/{history_id}")
def get_upload_status(history_id: int):
    row = get_history_by_id(history_id)
    if not row:
        raise HTTPException(status_code=404, detail="History entry not found.")
    return row


@router.get("/video/{video_id}/thumbnail")
def get_thumbnail(video_id: str):
    thumb = TMP_DIR / f"{video_id}_thumb.jpg"
    if not thumb.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found.")
    return FileResponse(str(thumb), media_type="image/jpeg")
