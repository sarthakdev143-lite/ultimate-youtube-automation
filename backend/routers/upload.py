"""Upload router — immediate + scheduled uploads, and video streaming."""
from html import escape
import json
import mimetypes
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload
from pydantic import BaseModel, Field

from db import get_history_by_id, insert_history, update_history_status
from utils import (
    BACKEND_DIR, TMP_DIR, authenticate_youtube_account, cleanup_old_tmp_files, find_video_path, get_youtube_service,
    check_quota, increment_quota, get_oauth_auth_url, exchange_oauth_code, get_quota_used, run_ffmpeg
)

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
    thumbnail_video_id: str = ""
    thumbnail_at_sec: float = 1.0
    webhook_url: str = ""


@router.post("/upload")
def upload_to_youtube(body: UploadBody):
    cleanup_old_tmp_files()

    if body.privacy not in ("public", "unlisted", "private"):
        raise HTTPException(status_code=400, detail='privacy must be "public", "unlisted", or "private".')

    path = find_video_path(body.video_id)

    # ── Upload Configuration ──────────────────────────────────────────────
    check_quota(body.youtube_account, 1600)
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

    if body.scheduled_at:
        try:
            import datetime
            datetime.datetime.fromisoformat(body.scheduled_at.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid scheduled_at. Use ISO 8601 format.")
        request_body["status"]["privacyStatus"] = "private"
        request_body["status"]["publishAt"] = body.scheduled_at
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
    
    # ── Custom Thumbnail ──
    if body.thumbnail_video_id:
        thumb_path = TMP_DIR / f"{body.video_id}_custom_tn.jpg"
        src_vid = find_video_path(body.thumbnail_video_id)
        try:
            run_ffmpeg([
                "-ss", str(body.thumbnail_at_sec), "-i", str(src_vid), "-frames:v", "1",
                "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720",
                str(thumb_path),
            ])
            if thumb_path.is_file():
                youtube.thumbnails().set(
                    videoId=vid,
                    media_body=MediaFileUpload(str(thumb_path), mimetype="image/jpeg", resumable=True)
                ).execute()
        except:
            pass # ignore thumbnail failures
            
    increment_quota(body.youtube_account, 1600)
    
    # ── Webhook ──
    if body.webhook_url:
        import httpx
        from datetime import datetime as dt
        try:
            httpx.post(body.webhook_url, json={
                "event": "upload_complete",
                "youtube_url": yt_url,
                "title": body.title or "Untitled",
                "platform": body.platform,
                "video_id": body.video_id,
                "timestamp": dt.utcnow().isoformat() + "Z"
            }, timeout=5.0)
        except:
            pass

    history_status = "scheduled" if body.scheduled_at else "uploaded"
    insert_history(
        video_id=body.video_id,
        source_url=body.source_url,
        platform=body.platform,
        title=body.title or "Untitled",
        youtube_url=yt_url,
        status=history_status,
        scheduled_at=body.scheduled_at,
        youtube_account=body.youtube_account,
        privacy=body.privacy,
        description=body.description,
        tags_json=json.dumps(body.tags),
        webhook_url=body.webhook_url,
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

class AccountExchangeBody(BaseModel):
    account: str
    code: str

@router.get("/youtube/accounts/auth-url")
def get_auth_url(account: str, request: Request):
    if not account.strip():
        raise HTTPException(status_code=400, detail="Account name is required.")
    callback_url = str(request.url_for("youtube_oauth_callback"))
    return {"auth_url": get_oauth_auth_url(account.strip(), callback_url)}

@router.post("/youtube/accounts/exchange")
def exchange_code(body: AccountExchangeBody):
    if not body.account.strip() or not body.code.strip():
        raise HTTPException(status_code=400, detail="Account and code are required.")
    account = exchange_oauth_code(body.account.strip(), body.code.strip())
    return {"success": True, "account": account}


def _oauth_callback_html(status: str, message: str, account: str = "") -> str:
    safe_status = escape(status)
    safe_message = escape(message)
    safe_account = escape(account)
    return f"""<!doctype html>
<html>
  <head>
    <meta charset="utf-8" />
    <title>YouTube OAuth</title>
    <style>
      body {{ font-family: system-ui, sans-serif; background: #0a0a0a; color: #e5e5e5; padding: 32px; }}
      .card {{ max-width: 560px; margin: 48px auto; padding: 24px; border: 1px solid #2a2a2a; border-radius: 16px; background: #111111; }}
      .ok {{ color: #10b981; }}
      .err {{ color: #f87171; }}
      p {{ line-height: 1.5; }}
    </style>
  </head>
  <body>
    <div class="card">
      <h1 class="{ 'ok' if status == 'success' else 'err' }">{safe_status.title()}</h1>
      <p>{safe_message}</p>
      <p>You can close this tab.</p>
    </div>
    <script>
      (function() {{
        if (window.opener) {{
          window.opener.postMessage({{
            type: "youtube-oauth-complete",
            status: "{safe_status}",
            account: "{safe_account}",
            message: "{safe_message}"
          }}, "*");
        }}
        if ("{safe_status}" === "success") {{
          setTimeout(function() {{ window.close(); }}, 1200);
        }}
      }})();
    </script>
  </body>
</html>"""


@router.get("/youtube/oauth/callback", response_class=HTMLResponse, name="youtube_oauth_callback")
def youtube_oauth_callback(request: Request, error: str | None = None):
    if error:
        return HTMLResponse(
            _oauth_callback_html("error", f"Google OAuth failed: {error}"),
            status_code=400,
        )
    try:
        account = exchange_oauth_code("", str(request.url))
    except HTTPException as exc:
        return HTMLResponse(
            _oauth_callback_html("error", str(exc.detail)),
            status_code=exc.status_code,
        )
    return HTMLResponse(
        _oauth_callback_html("success", f"Connected YouTube account: {account}", account),
        status_code=200,
    )

@router.get("/quota/{account}")
def get_quota(account: str):
    used = get_quota_used(account)
    return {
        "account": account,
        "used": used,
        "remaining": max(0, 10000 - used),
        "uploads_remaining": max(0, (10000 - used) // 1600)
    }


@router.get("/upload/status/{history_id}")
def get_upload_status(history_id: int):
    row = get_history_by_id(history_id)
    if not row:
        raise HTTPException(status_code=404, detail="History entry not found.")
    return row


@router.get("/video/{video_id}/thumbnail")
def get_thumbnail(video_id: str, t: float | None = None):
    if t is not None:
        try:
            video_path = find_video_path(video_id)
            out_path = TMP_DIR / f"{video_id}_t{t}.jpg"
            if not out_path.is_file():
                run_ffmpeg([
                    "-ss", str(t), "-i", str(video_path), "-frames:v", "1",
                    "-vf", "scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720",
                    str(out_path)
                ])
            return FileResponse(str(out_path), media_type="image/jpeg")
        except Exception:
            pass
            
    thumb = TMP_DIR / f"{video_id}_thumb.jpg"
    if not thumb.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found.")
    return FileResponse(str(thumb), media_type="image/jpeg")
