"""Telegram bot bridge for existing YT automation pipeline."""
import asyncio
import base64
import json
import logging
import os
from io import BytesIO
from typing import Any

from fastapi import HTTPException
from groq import Groq
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from db import get_history
from routers.analytics import get_analytics
from routers.download import DownloadBody, download_video
from routers.edit import EditBody, edit_video
from routers.upload import UploadBody, upload_to_youtube
from utils import detect_platform, get_quota_used

logger = logging.getLogger(__name__)

SUPPORTED_MSG = (
    "Instagram, TikTok, YouTube Shorts, Twitter/X, Reddit, Snapchat, Pinterest."
)

_app: Application | None = None
_bot_task: asyncio.Task | None = None
_allowed_chat_ids: set[int] | None = None
_pending: dict[int, dict[str, Any]] = {}


def _parse_allowed_chat_ids() -> set[int] | None:
    raw = os.getenv("TELEGRAM_ALLOWED_CHAT_IDS", "").strip()
    if not raw:
        logger.warning("TELEGRAM_ALLOWED_CHAT_IDS is empty; allowing all chats (dev mode).")
        return None
    parsed: set[int] = set()
    for part in raw.split(","):
        text = part.strip()
        if not text:
            continue
        try:
            parsed.add(int(text))
        except ValueError:
            logger.warning("Ignoring invalid chat id in TELEGRAM_ALLOWED_CHAT_IDS: %s", text)
    if not parsed:
        logger.warning("No valid TELEGRAM_ALLOWED_CHAT_IDS found; allowing all chats (dev mode).")
        return None
    return parsed


def _default_account() -> str:
    return os.getenv("TELEGRAM_DEFAULT_ACCOUNT", "default").strip() or "default"


def _default_privacy() -> str:
    privacy = (os.getenv("TELEGRAM_DEFAULT_PRIVACY", "private").strip() or "private").lower()
    return privacy if privacy in {"public", "unlisted", "private"} else "private"


def _watermark_text() -> str:
    return os.getenv("TELEGRAM_WATERMARK", "").strip()


def _is_authorized(chat_id: int) -> bool:
    return _allowed_chat_ids is None or chat_id in _allowed_chat_ids


async def _require_authorized(update: Update) -> bool:
    if not update.effective_message or not update.effective_chat:
        return False
    chat_id = update.effective_chat.id
    if _is_authorized(chat_id):
        return True
    await update.effective_message.reply_text("⛔ Unauthorized.")
    return False


async def _send_ready_message(update: Update, state: dict[str, Any], ai_fallback: bool) -> None:
    tags = state.get("tags", [])
    ai_note = "\n(AI unavailable — using original metadata)" if ai_fallback else ""
    text = (
        "✅ Ready to upload\n\n"
        f"📝 Title: {state.get('title', '')}\n"
        f"📄 Description: {(state.get('description', '')[:100])}...\n"
        f"🏷 Tags: {', '.join(tags[:5]) if tags else 'none'}\n"
        f"🔒 Privacy: {state.get('privacy', _default_privacy())}\n"
        f"💧 Watermark: {_watermark_text() or 'none'}"
        f"{ai_note}\n\n"
        "Reply with:\n"
        "/upload — upload now\n"
        "/title New Title Here — change title\n"
        "/desc New description — change description\n"
        "/tags tag1, tag2, tag3 — change tags\n"
        "/privacy public|unlisted|private — change privacy\n"
        "/cancel — cancel"
    )
    await update.effective_message.reply_text(text)


def _generate_ai_metadata(platform: str, title: str, description: str, duration: float) -> dict[str, Any]:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY missing")
    client = Groq(api_key=api_key)
    prompt = f"""Generate YouTube metadata for this video:
Platform: {platform}
Original title: {title}
Description: {description}
Duration: {duration}s

Return a JSON object with exactly these keys:
- title: engaging YouTube title with emojis, max 90 characters
- description: SEO-optimized description
- tags: array of strings, no # symbol, max 30 items

Respond with ONLY valid JSON. No markdown, no explanation."""
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=[
            {
                "role": "system",
                "content": "You are a YouTube SEO expert. Respond only with valid JSON, no markdown.",
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.7,
        max_tokens=1000,
    )
    raw = (response.choices[0].message.content or "").strip()
    cleaned = raw.replace("```json", "").replace("```", "").strip()
    data = json.loads(cleaned)
    tags = data.get("tags", [])
    return {
        "title": str(data.get("title", title))[:90],
        "description": str(data.get("description", description)),
        "tags": [str(t).strip().lstrip("#") for t in tags if str(t).strip()][:30],
    }


def _resolve_history_id(youtube_url: str) -> int | None:
    for row in get_history(limit=20):
        if row.get("youtube_url") == youtube_url:
            return int(row["id"])
    return None


async def _handle_url(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_chat and update.effective_message
    chat_id = update.effective_chat.id
    url = (update.effective_message.text or "").strip()
    if not url:
        return
    if _pending.get(chat_id):
        await update.effective_message.reply_text(
            "You have a pending upload. /cancel it first or /upload it."
        )
        return

    platform = detect_platform(url)
    if platform is None:
        await update.effective_message.reply_text(
            f"❌ Unsupported URL. Supported: {SUPPORTED_MSG}"
        )
        return

    await update.effective_message.reply_text("⬇️ Downloading...")
    try:
        result = await asyncio.to_thread(download_video, DownloadBody(url=url))
    except HTTPException as exc:
        await update.effective_message.reply_text(
            f"❌ Download failed: {exc.detail}. Check the URL and try again."
        )
        return
    except Exception as exc:
        await update.effective_message.reply_text(
            f"❌ Download failed: {exc}. Check the URL and try again."
        )
        return

    thumb_b64 = result.get("thumbnail", "")
    if thumb_b64:
        try:
            await update.effective_message.reply_photo(photo=BytesIO(base64.b64decode(thumb_b64)))
        except Exception:
            logger.exception("Failed sending thumbnail for chat %s", chat_id)

    await update.effective_message.reply_text(
        "📹 Title: {title}\n"
        "🌐 Platform: {platform}\n"
        "⏱ Duration: {duration}s\n\n"
        "✨ Generating AI metadata...".format(
            title=result.get("title", ""),
            platform=result.get("platform_label", platform),
            duration=int(float(result.get("duration") or 0)),
        )
    )

    original_title = result.get("title", "")
    original_desc = result.get("description", "")
    original_tags = result.get("tags", []) or []
    ai_fallback = False
    try:
        ai_data = await asyncio.to_thread(
            _generate_ai_metadata,
            str(result.get("platform_label", platform)),
            str(original_title),
            str(original_desc),
            float(result.get("duration") or 0),
        )
    except Exception:
        ai_fallback = True
        ai_data = {
            "title": original_title,
            "description": original_desc,
            "tags": original_tags[:30],
        }

    _pending[chat_id] = {
        "video_id": result["video_id"],
        "title": ai_data["title"] or original_title,
        "description": ai_data["description"] or original_desc,
        "tags": ai_data["tags"] or original_tags[:30],
        "privacy": _default_privacy(),
        "platform": platform,
        "source_url": url,
        "duration": float(result.get("duration") or 0),
    }
    await _send_ready_message(update, _pending[chat_id], ai_fallback)


async def _start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    await update.effective_message.reply_text(
        "Welcome to YT Automation Bot.\n\n"
        "Send any supported video URL to begin.\n"
        "Commands: /upload, /title, /desc, /tags, /privacy, /history, /quota, /stats, /cancel"
    )


async def _cancel(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_chat and update.effective_message
    _pending.pop(update.effective_chat.id, None)
    await update.effective_message.reply_text("❌ Cancelled.")


async def _upload(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_chat and update.effective_message
    chat_id = update.effective_chat.id
    state = _pending.get(chat_id)
    if not state:
        await update.effective_message.reply_text("No pending upload. Send a URL first.")
        return

    await update.effective_message.reply_text("⬆️ Uploading to YouTube...")
    video_id = state["video_id"]
    wm = _watermark_text()
    if wm:
        try:
            edit_res = await asyncio.to_thread(
                edit_video, EditBody(video_id=video_id, watermark_text=wm)
            )
            video_id = edit_res["edited_video_id"]
        except Exception:
            logger.exception("Watermark apply failed for chat %s; continuing with original video", chat_id)

    try:
        result = await asyncio.to_thread(
            upload_to_youtube,
            UploadBody(
                video_id=video_id,
                title=state["title"],
                description=state["description"],
                tags=state["tags"],
                privacy=state["privacy"],
                source_url=state["source_url"],
                platform=state["platform"],
                youtube_account=_default_account(),
            ),
        )
    except HTTPException as exc:
        msg = str(exc.detail)
        if exc.status_code == 429:
            await update.effective_message.reply_text(
                "❌ YouTube quota exhausted for today. Try again after midnight UTC."
            )
        else:
            await update.effective_message.reply_text(f"❌ Upload failed: {msg}")
        return
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ Upload failed: {exc}")
        return

    youtube_url = result.get("youtube_url", "")
    history_id = _resolve_history_id(youtube_url)
    history_text = f"{history_id}" if history_id else "latest"
    _pending.pop(chat_id, None)
    await update.effective_message.reply_text(
        "🎉 Uploaded!\n\n"
        f"📺 {youtube_url}\n"
        f"📊 Check analytics in 48h with /stats {history_text}"
    )


async def _set_field(update: Update, field: str, value: str) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_chat and update.effective_message
    state = _pending.get(update.effective_chat.id)
    if not state:
        await update.effective_message.reply_text("No pending upload. Send a URL first.")
        return
    if not value.strip():
        await update.effective_message.reply_text("Please provide a value.")
        return
    state[field] = value.strip()
    await _send_ready_message(update, state, False)


async def _title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_field(update, "title", " ".join(context.args))


async def _desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_field(update, "description", " ".join(context.args))


async def _tags(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_chat and update.effective_message
    state = _pending.get(update.effective_chat.id)
    if not state:
        await update.effective_message.reply_text("No pending upload. Send a URL first.")
        return
    raw = " ".join(context.args).strip()
    tags = [t.strip().lstrip("#") for t in raw.split(",") if t.strip()][:30]
    if not tags:
        await update.effective_message.reply_text("Provide comma-separated tags.")
        return
    state["tags"] = tags
    await _send_ready_message(update, state, False)


async def _privacy(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_chat and update.effective_message
    state = _pending.get(update.effective_chat.id)
    if not state:
        await update.effective_message.reply_text("No pending upload. Send a URL first.")
        return
    value = (context.args[0] if context.args else "").strip().lower()
    if value not in {"public", "unlisted", "private"}:
        await update.effective_message.reply_text("Usage: /privacy public|unlisted|private")
        return
    state["privacy"] = value
    await _send_ready_message(update, state, False)


async def _quota(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    used = await asyncio.to_thread(get_quota_used, _default_account())
    remaining = max(0, 10000 - used)
    uploads_left = remaining // 1600
    await update.effective_message.reply_text(
        f"🔋 Quota: {used}/10,000 units (~{uploads_left} uploads left)"
    )


async def _history(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    rows = await asyncio.to_thread(get_history, 5, 0)
    if not rows:
        await update.effective_message.reply_text("No upload history yet.")
        return
    lines = ["Last 5 uploads:"]
    for row in rows:
        link = row.get("youtube_url") or "no link yet"
        lines.append(f"- #{row['id']} | {row.get('status', 'unknown')} | {link}")
    await update.effective_message.reply_text("\n".join(lines))


async def _stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_message
    if not context.args:
        await update.effective_message.reply_text("Usage: /stats <history_id>")
        return
    try:
        history_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text("history_id must be a number.")
        return
    try:
        stats = await asyncio.to_thread(get_analytics, history_id)
    except HTTPException as exc:
        await update.effective_message.reply_text(f"❌ {exc.detail}")
        return
    await update.effective_message.reply_text(
        "📊 Stats for \"{title}\"\n\n"
        "👁 {views} views\n"
        "👍 {likes} likes\n"
        "💬 {comments} comments".format(
            title=stats.get("title", ""),
            views=stats.get("views", 0),
            likes=stats.get("likes", 0),
            comments=stats.get("comments", 0),
        )
    )


async def _handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Telegram bot error: %s", context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(f"❌ Upload failed: {context.error}")
        except Exception:
            logger.exception("Failed sending error back to Telegram user")


async def _run_bot(token: str) -> None:
    global _app
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", _start))
    app.add_handler(CommandHandler("cancel", _cancel))
    app.add_handler(CommandHandler("upload", _upload))
    app.add_handler(CommandHandler("title", _title))
    app.add_handler(CommandHandler("desc", _desc))
    app.add_handler(CommandHandler("tags", _tags))
    app.add_handler(CommandHandler("privacy", _privacy))
    app.add_handler(CommandHandler("quota", _quota))
    app.add_handler(CommandHandler("history", _history))
    app.add_handler(CommandHandler("stats", _stats))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_url))
    app.add_error_handler(_handle_error)
    _app = app
    await app.initialize()
    await app.start()
    await app.updater.start_polling()


async def _run_bot_with_restart(token: str) -> None:
    while True:
        try:
            await _run_bot(token)
        except Exception as exc:
            logger.exception("Bot crashed, restarting in 5s: %s", exc)
            await asyncio.sleep(5)

async def start_telegram_bot() -> None:
    global _bot_task, _allowed_chat_ids
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        logger.warning("TELEGRAM_BOT_TOKEN not set — bot disabled.")
        return
    _allowed_chat_ids = _parse_allowed_chat_ids()
    _bot_task = asyncio.create_task(_run_bot_with_restart(token))


async def stop_telegram_bot() -> None:
    global _app, _bot_task
    if _app:
        try:
            if _app.updater:
                await _app.updater.stop()
            await _app.stop()
            await _app.shutdown()
        finally:
            _app = None
    if _bot_task and not _bot_task.done():
        _bot_task.cancel()
        try:
            await _bot_task
        except asyncio.CancelledError:
            pass
    _bot_task = None
