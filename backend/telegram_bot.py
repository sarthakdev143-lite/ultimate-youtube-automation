"""Telegram bot bridge for existing YT automation pipeline."""
import asyncio
import base64
import json
import logging
import os
import time
from datetime import UTC, datetime, timedelta
from functools import wraps
from io import BytesIO
from typing import Any

from fastapi import HTTPException
from groq import Groq
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

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
_runtime: dict[str, Any] = {
    "started_at": None,
    "last_ok_at": None,
    "last_error_at": None,
    "last_error": "",
    "last_command": "",
    "restart_count": 0,
}
_ACTION_UPLOAD = "upload"
_ACTION_CANCEL = "cancel"
_ACTION_CLEAR_SCHEDULE = "schedule:clear"
_ACTION_SCHEDULE_30M = "schedule:30m"
_ACTION_SCHEDULE_1H = "schedule:1h"
_ACTION_SCHEDULE_6H = "schedule:6h"
_ACTION_SCHEDULE_24H = "schedule:24h"


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "never"
    return dt.isoformat().replace("+00:00", "Z")


def _fmt_age(dt: datetime | None) -> str:
    if not dt:
        return "n/a"
    seconds = max(0, int((_now_utc() - dt).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m"


def _parse_schedule_iso(raw: str) -> datetime:
    text = raw.strip()
    if not text:
        raise ValueError("missing datetime")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    if parsed <= _now_utc():
        raise ValueError("schedule time must be in the future")
    return parsed


def _quick_schedule_from_token(raw: str) -> datetime:
    token = raw.strip().lower()
    now = _now_utc()
    if token.endswith("m") and token[:-1].isdigit():
        return now + timedelta(minutes=int(token[:-1]))
    if token.endswith("h") and token[:-1].isdigit():
        return now + timedelta(hours=int(token[:-1]))
    if token.endswith("d") and token[:-1].isdigit():
        return now + timedelta(days=int(token[:-1]))
    if token in {"tomorrow9", "tomorrow9am", "tomorrow-9", "tomorrow-9am"}:
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=9, minute=0, second=0, microsecond=0)
    raise ValueError("unsupported quick schedule format")


def _parse_schedule_input(raw: str) -> datetime:
    try:
        dt = _quick_schedule_from_token(raw)
    except ValueError:
        return _parse_schedule_iso(raw)
    if dt <= _now_utc():
        raise ValueError("schedule time must be in the future")
    return dt


def _main_menu_markup() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["⬆️ Upload now", "🗓 +1h"],
            ["🗓 +6h", "🗓 +24h"],
            ["📋 Status", "🩺 Health"],
            ["📚 History", "🔋 Quota"],
            ["❌ Cancel", "ℹ️ Help"],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
    )


def _ready_actions_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬆️ Upload now", callback_data=_ACTION_UPLOAD)],
            [
                InlineKeyboardButton("+30m", callback_data=_ACTION_SCHEDULE_30M),
                InlineKeyboardButton("+1h", callback_data=_ACTION_SCHEDULE_1H),
                InlineKeyboardButton("+6h", callback_data=_ACTION_SCHEDULE_6H),
            ],
            [
                InlineKeyboardButton("+24h", callback_data=_ACTION_SCHEDULE_24H),
                InlineKeyboardButton("Clear schedule", callback_data=_ACTION_CLEAR_SCHEDULE),
            ],
            [InlineKeyboardButton("❌ Cancel", callback_data=_ACTION_CANCEL)],
        ]
    )


def _set_ok(command: str) -> None:
    _runtime["last_ok_at"] = _now_utc()
    _runtime["last_command"] = command


def _set_error(err: Exception) -> None:
    _runtime["last_error_at"] = _now_utc()
    _runtime["last_error"] = str(err)[:240]


def _safe_user_error(err: Exception) -> str:
    if isinstance(err, HTTPException):
        detail = str(err.detail)
        return detail if detail else "Request failed."
    return "Unexpected error while processing command."


def _guard(command_name: str):
    def decorator(func):
        @wraps(func)
        async def wrapped(update: Update, context: ContextTypes.DEFAULT_TYPE):
            try:
                await func(update, context)
                _set_ok(command_name)
            except Exception as exc:
                _set_error(exc)
                logger.exception("Command %s failed: %s", command_name, exc)
                if isinstance(update, Update) and update.effective_message:
                    try:
                        await update.effective_message.reply_text(f"❌ {_safe_user_error(exc)}")
                    except Exception:
                        logger.exception("Failed sending guarded error to Telegram user")
        return wrapped
    return decorator


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
    privacy = (os.getenv("TELEGRAM_DEFAULT_PRIVACY", "public").strip() or "public").lower()
    return privacy if privacy in {"public", "unlisted", "private"} else "public"


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
    scheduled_at = state.get("scheduled_at")
    schedule_note = scheduled_at if scheduled_at else "none (uploads immediately)"
    ai_note = "\n(AI unavailable — using original metadata)" if ai_fallback else ""
    text = (
        "✅ Ready\n\n"
        f"📝 {state.get('title', '')[:80]}\n"
        f"🔒 Privacy: {state.get('privacy', _default_privacy())}\n"
        f"🗓 Schedule: {schedule_note}\n"
        f"🏷 Tags: {', '.join(tags[:4]) if tags else 'none'}\n"
        f"💧 Watermark: {_watermark_text() or 'none'}"
        f"{ai_note}\n\n"
        "Tap a button below, or edit with:\n"
        "/title ... | /desc ... | /tags a, b | /privacy public|unlisted|private\n"
        "/schedule <ISO> or quick format: /schedule 30m, /schedule 6h, /schedule tomorrow9am\n\n"
        "Smart reply wizard: reply with `1` (now), `2` (+1h), `3` (+6h), `4` (+24h), `5` (custom time)"
    )
    await update.effective_message.reply_text(
        text,
        reply_markup=_ready_actions_markup(),
    )


async def _run_wizard_choice(update: Update, context: ContextTypes.DEFAULT_TYPE, choice: str) -> bool:
    assert update.effective_chat and update.effective_message
    state = _pending.get(update.effective_chat.id)
    if not state:
        return False
    normalized = choice.strip().lower()
    if normalized in {"1", "now"}:
        state["scheduled_at"] = None
        await _upload(update, context)
        return True
    if normalized in {"2", "1h", "+1h"}:
        context.args = ["1h"]
        await _schedule(update, context)
        await _upload(update, context)
        return True
    if normalized in {"3", "6h", "+6h"}:
        context.args = ["6h"]
        await _schedule(update, context)
        await _upload(update, context)
        return True
    if normalized in {"4", "24h", "+24h"}:
        context.args = ["24h"]
        await _schedule(update, context)
        await _upload(update, context)
        return True
    if normalized in {"5", "custom"}:
        state["wizard_step"] = "await_custom_schedule"
        await update.effective_message.reply_text(
            "Send custom schedule time now.\nExamples: `2026-04-15T19:30:00Z`, `30m`, `2h`, `tomorrow9am`"
        )
        return True
    if state.get("wizard_step") == "await_custom_schedule":
        try:
            parsed = _parse_schedule_input(choice)
        except ValueError:
            await update.effective_message.reply_text(
                "Could not parse that time. Try: `30m`, `2h`, `tomorrow9am`, or `2026-04-15T19:30:00Z`"
            )
            return True
        state["wizard_step"] = None
        state["scheduled_at"] = parsed.isoformat().replace("+00:00", "Z")
        await _upload(update, context)
        return True
    return False


def _looks_like_url(text: str) -> bool:
    t = text.strip().lower()
    return t.startswith("http://") or t.startswith("https://")


async def _dispatch_text_action(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    normalized = text.strip().lower()
    if normalized in {"⬆️ upload now", "upload now", "upload"}:
        await _upload(update, context)
        return True
    if normalized in {"❌ cancel", "cancel"}:
        await _cancel(update, context)
        return True
    if normalized in {"📋 status", "status"}:
        await _status(update, context)
        return True
    if normalized in {"🩺 health", "health"}:
        await _health(update, context)
        return True
    if normalized in {"📚 history", "history"}:
        await _history(update, context)
        return True
    if normalized in {"🔋 quota", "quota"}:
        await _quota(update, context)
        return True
    if normalized in {"ℹ️ help", "help"}:
        await _help(update, context)
        return True
    if normalized in {"🗓 +1h", "+1h"}:
        context.args = ["1h"]
        await _schedule(update, context)
        return True
    if normalized in {"🗓 +6h", "+6h"}:
        context.args = ["6h"]
        await _schedule(update, context)
        return True
    if normalized in {"🗓 +24h", "+24h"}:
        context.args = ["24h"]
        await _schedule(update, context)
        return True
    return False


@_guard("action_cb")
async def _action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    query = update.callback_query
    if not query:
        return
    await query.answer()
    data = query.data or ""
    if data == _ACTION_UPLOAD:
        await _upload(update, context)
        return
    if data == _ACTION_CANCEL:
        await _cancel(update, context)
        return
    if data == _ACTION_CLEAR_SCHEDULE:
        context.args = ["clear"]
        await _schedule(update, context)
        return
    if data == _ACTION_SCHEDULE_30M:
        context.args = ["30m"]
        await _schedule(update, context)
        return
    if data == _ACTION_SCHEDULE_1H:
        context.args = ["1h"]
        await _schedule(update, context)
        return
    if data == _ACTION_SCHEDULE_6H:
        context.args = ["6h"]
        await _schedule(update, context)
        return
    if data == _ACTION_SCHEDULE_24H:
        context.args = ["24h"]
        await _schedule(update, context)
        return


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


@_guard("url")
async def _handle_url(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_chat and update.effective_message
    chat_id = update.effective_chat.id
    context = _
    text = (update.effective_message.text or "").strip()
    if not text:
        return
    if _pending.get(chat_id):
        if await _run_wizard_choice(update, context, text):
            return
    handled_action = await _dispatch_text_action(update, context, text)
    if handled_action:
        return
    if not _looks_like_url(text):
        await update.effective_message.reply_text(
            "Send a reel/short URL, or use the menu buttons below for one-tap actions.",
            reply_markup=_main_menu_markup(),
        )
        return
    url = text
    if _pending.get(chat_id):
        await update.effective_message.reply_text(
            "You already have a pending draft. Reply `1` to upload now, `5` for custom schedule, or tap menu actions."
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
        "wizard_step": "await_choice",
    }
    await _send_ready_message(update, _pending[chat_id], ai_fallback)


@_guard("start")
async def _start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    await update.effective_message.reply_text(
        "Welcome to YT Automation Bot.\n\n"
        "Fastest flow:\n"
        "1) Send a reel/short URL\n"
        "2) Tap Upload now or a schedule button\n\n"
        "Use /menu anytime for one-tap controls.",
        reply_markup=_main_menu_markup(),
    )


@_guard("menu")
async def _menu(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_message
    await update.effective_message.reply_text(
        "Main actions are pinned below. Send a URL to start a new draft.",
        reply_markup=_main_menu_markup(),
    )


@_guard("help")
async def _help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_message
    await update.effective_message.reply_text(
        "Quick help:\n"
        "- Send URL -> draft auto-created\n"
        "- Tap Upload now / +1h / +6h / +24h\n"
        "- Fine tune: /title, /desc, /tags, /privacy\n"
        "- Set exact UTC: /schedule 2026-04-15T19:30:00Z\n"
        "- Quick schedule: /schedule 30m | 2h | tomorrow9am\n"
        "- Health: /health, current draft: /status",
        reply_markup=_main_menu_markup(),
    )


@_guard("health")
async def _health(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_message
    started_at = _runtime.get("started_at")
    last_ok = _runtime.get("last_ok_at")
    last_error_at = _runtime.get("last_error_at")
    lines = [
        "🩺 Bot Health",
        f"• Bot running: {'yes' if _app is not None else 'no'}",
        f"• Uptime: {_fmt_age(started_at) if isinstance(started_at, datetime) else 'n/a'}",
        f"• Pending chats: {len(_pending)}",
        f"• Last successful command: {_runtime.get('last_command') or 'none'} ({_fmt_dt(last_ok) if isinstance(last_ok, datetime) else 'never'})",
        f"• Last error: {_runtime.get('last_error') or 'none'} ({_fmt_dt(last_error_at) if isinstance(last_error_at, datetime) else 'never'})",
        f"• Restart count: {_runtime.get('restart_count', 0)}",
    ]
    await update.effective_message.reply_text("\n".join(lines))


@_guard("ping")
async def _ping(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_message
    start = time.perf_counter()
    latency_ms = int((time.perf_counter() - start) * 1000)
    await update.effective_message.reply_text(f"🏓 Pong ({latency_ms}ms)")


@_guard("status")
async def _status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_chat and update.effective_message
    state = _pending.get(update.effective_chat.id)
    if not state:
        await update.effective_message.reply_text("No pending upload in this chat.")
        return
    tags = state.get("tags", [])
    await update.effective_message.reply_text(
        "📋 Pending status\n\n"
        f"🆔 Video ID: {state.get('video_id', '')}\n"
        f"📝 Title: {state.get('title', '')}\n"
        f"🔒 Privacy: {state.get('privacy', _default_privacy())}\n"
        f"🗓 Schedule: {state.get('scheduled_at') or 'none (uploads immediately)'}\n"
        f"🏷 Tags: {', '.join(tags[:5]) if tags else 'none'}"
    )


@_guard("pending")
async def _pending_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_message
    if not _pending:
        await update.effective_message.reply_text("No pending uploads.")
        return
    lines = [f"Pending uploads: {len(_pending)}"]
    for chat_id, state in list(_pending.items())[:10]:
        lines.append(
            f"- chat {chat_id}: {state.get('title', 'Untitled')[:40]} | schedule: {state.get('scheduled_at') or 'none'}"
        )
    await update.effective_message.reply_text("\n".join(lines))


@_guard("schedule")
async def _schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_chat and update.effective_message
    state = _pending.get(update.effective_chat.id)
    if not state:
        await update.effective_message.reply_text("No pending upload. Send a URL first.")
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await update.effective_message.reply_text(
            "Usage: /schedule 2026-04-15T19:30:00Z\nUse UTC ISO format."
        )
        return
    if raw.lower() in {"none", "clear", "off"}:
        state["scheduled_at"] = None
        await _send_ready_message(update, state, False)
        return
    try:
        parsed = _parse_schedule_input(raw)
    except ValueError as exc:
        await update.effective_message.reply_text(f"Invalid schedule time: {exc}")
        return
    state["scheduled_at"] = parsed.isoformat().replace("+00:00", "Z")
    await _send_ready_message(update, state, False)


@_guard("scheduled")
async def _scheduled(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_message
    rows = await asyncio.to_thread(get_history, 25, 0)
    scheduled_rows = [r for r in rows if r.get("status") == "scheduled" or r.get("scheduled_at")]
    if not scheduled_rows:
        await update.effective_message.reply_text("No scheduled uploads found in recent history.")
        return
    lines = ["Upcoming/Recorded scheduled uploads:"]
    for row in scheduled_rows[:10]:
        lines.append(
            f"- #{row['id']} | {row.get('scheduled_at') or 'n/a'} | {row.get('status', 'unknown')} | {row.get('youtube_url') or 'no link'}"
        )
    await update.effective_message.reply_text("\n".join(lines))


@_guard("unschedule")
async def _unschedule(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_message
    await update.effective_message.reply_text(
        "Unschedule is not supported yet in backend jobs. For now, delete from UI/DB or re-upload with new schedule."
    )


@_guard("cancel")
async def _cancel(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_chat and update.effective_message
    _pending.pop(update.effective_chat.id, None)
    await update.effective_message.reply_text("❌ Cancelled.")


@_guard("upload")
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
                scheduled_at=state.get("scheduled_at"),
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
    if state.get("scheduled_at"):
        await update.effective_message.reply_text(
            "🗓 Scheduled successfully!\n\n"
            f"📺 {youtube_url}\n"
            f"⏰ Publish at: {state.get('scheduled_at')}\n"
            f"📊 Check analytics after publish with /stats {history_text}"
        )
    else:
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


@_guard("title")
async def _title(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_field(update, "title", " ".join(context.args))


@_guard("desc")
async def _desc(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _set_field(update, "description", " ".join(context.args))


@_guard("tags")
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


@_guard("privacy")
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


@_guard("quota")
async def _quota(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    used = await asyncio.to_thread(get_quota_used, _default_account())
    remaining = max(0, 10000 - used)
    uploads_left = remaining // 1600
    await update.effective_message.reply_text(
        f"🔋 Quota: {used}/10,000 units (~{uploads_left} uploads left)"
    )


@_guard("history")
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


@_guard("stats")
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
    app.add_handler(CommandHandler("menu", _menu))
    app.add_handler(CommandHandler("help", _help))
    app.add_handler(CommandHandler("health", _health))
    app.add_handler(CommandHandler("ping", _ping))
    app.add_handler(CommandHandler("status", _status))
    app.add_handler(CommandHandler("pending", _pending_cmd))
    app.add_handler(CommandHandler("schedule", _schedule))
    app.add_handler(CommandHandler("scheduled", _scheduled))
    app.add_handler(CommandHandler("unschedule", _unschedule))
    app.add_handler(CommandHandler("cancel", _cancel))
    app.add_handler(CommandHandler("upload", _upload))
    app.add_handler(CommandHandler("title", _title))
    app.add_handler(CommandHandler("desc", _desc))
    app.add_handler(CommandHandler("tags", _tags))
    app.add_handler(CommandHandler("privacy", _privacy))
    app.add_handler(CommandHandler("quota", _quota))
    app.add_handler(CommandHandler("history", _history))
    app.add_handler(CommandHandler("stats", _stats))
    app.add_handler(CallbackQueryHandler(_action_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_url))
    app.add_error_handler(_handle_error)
    _app = app
    _runtime["started_at"] = _now_utc()
    _runtime["last_error"] = ""
    await app.initialize()
    await app.start()
    await app.updater.start_polling()


async def _run_bot_with_restart(token: str) -> None:
    while True:
        try:
            await _run_bot(token)
        except Exception as exc:
            _runtime["restart_count"] = int(_runtime.get("restart_count", 0)) + 1
            _set_error(exc)
            logger.exception("Bot crashed, restarting in 5s: %s", exc)
            await asyncio.sleep(5)


def get_telegram_runtime_status() -> dict[str, Any]:
    started_at = _runtime.get("started_at")
    last_ok = _runtime.get("last_ok_at")
    last_error_at = _runtime.get("last_error_at")
    return {
        "enabled": bool(os.getenv("TELEGRAM_BOT_TOKEN", "").strip()),
        "running": _app is not None,
        "uptime_seconds": int((_now_utc() - started_at).total_seconds()) if isinstance(started_at, datetime) else 0,
        "pending_chats": len(_pending),
        "last_command": _runtime.get("last_command") or "",
        "last_ok_at": _fmt_dt(last_ok) if isinstance(last_ok, datetime) else None,
        "last_error": _runtime.get("last_error") or "",
        "last_error_at": _fmt_dt(last_error_at) if isinstance(last_error_at, datetime) else None,
        "restart_count": int(_runtime.get("restart_count", 0)),
    }


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
