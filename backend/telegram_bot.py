"""Telegram bot bridge for existing YT automation pipeline."""
import asyncio
import base64
import html
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from functools import wraps
from io import BytesIO
from typing import Any

from fastapi import HTTPException
from groq import Groq
from telegram import (
    ChatAction,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
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

# ── Callback data constants ───────────────────────────────────────────────────
_ACTION_UPLOAD = "upload"
_ACTION_CANCEL = "cancel"
_ACTION_CLEAR_SCHEDULE = "schedule:clear"
_ACTION_SCHEDULE_30M = "schedule:30m"
_ACTION_SCHEDULE_1H = "schedule:1h"
_ACTION_SCHEDULE_6H = "schedule:6h"
_ACTION_SCHEDULE_24H = "schedule:24h"
_ACTION_STATS = "stats"
_ACTION_SCHEDULED = "scheduled"
_ACTION_HISTORY = "history"
_ACTION_QUOTA = "quota"


# ── Utility helpers ───────────────────────────────────────────────────────────

def _esc(text: str) -> str:
    """HTML-escape text so it is safe inside HTML parse_mode messages."""
    return html.escape(str(text))


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
        return f"{seconds}s ago"
    if seconds < 3600:
        return f"{seconds // 60}m ago"
    return f"{seconds // 3600}h {(seconds % 3600) // 60}m ago"


def _build_quota_bar(used: int, total: int = 10_000, width: int = 10) -> str:
    """Return a unicode block progress bar, e.g. ████░░░░░░ 40%"""
    pct = min(used / total, 1.0)
    filled = round(pct * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"{bar}  {int(pct * 100)}%"


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


# ── Keyboards ─────────────────────────────────────────────────────────────────

def _main_menu_markup() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            ["⬆️ Upload Now", "🗓 +1h"],
            ["🗓 +6h", "🗓 +24h"],
            ["📋 Status", "🩺 Health"],
            ["📚 History", "🔋 Quota"],
            ["📊 Stats", "🗓 Scheduled"],
            ["❌ Cancel", "ℹ️ Help"],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
    )


def _ready_actions_markup() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("⬆️  Upload Now", callback_data=_ACTION_UPLOAD)],
            [
                InlineKeyboardButton("⏰ +30m", callback_data=_ACTION_SCHEDULE_30M),
                InlineKeyboardButton("⏰ +1h", callback_data=_ACTION_SCHEDULE_1H),
            ],
            [
                InlineKeyboardButton("⏰ +6h", callback_data=_ACTION_SCHEDULE_6H),
                InlineKeyboardButton("⏰ +24h", callback_data=_ACTION_SCHEDULE_24H),
            ],
            [
                InlineKeyboardButton("🗑 Clear Schedule", callback_data=_ACTION_CLEAR_SCHEDULE),
                InlineKeyboardButton("❌ Cancel", callback_data=_ACTION_CANCEL),
            ],
        ]
    )


# ── Runtime tracking ──────────────────────────────────────────────────────────

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


# ── Guard decorator ───────────────────────────────────────────────────────────

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
                        await update.effective_message.reply_text(
                            f"❌ <b>Error</b>\n{_esc(_safe_user_error(exc))}",
                            parse_mode="HTML",
                        )
                    except Exception:
                        logger.exception("Failed sending guarded error to Telegram user")
        return wrapped
    return decorator


# ── Auth helpers ──────────────────────────────────────────────────────────────

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
    await update.effective_message.reply_text(
        "⛔ <b>Unauthorized.</b>\nYou don't have permission to use this bot.",
        parse_mode="HTML",
    )
    return False


# ── Typing indicator ──────────────────────────────────────────────────────────

@asynccontextmanager
async def _typing(update: Update):
    """Send a typing action while a slow coroutine runs."""
    async def _keep_typing():
        while True:
            try:
                if update.effective_chat:
                    await update.effective_chat.send_action(ChatAction.TYPING)
            except Exception:
                pass
            await asyncio.sleep(4)

    task = asyncio.create_task(_keep_typing())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ── Rich message builders ─────────────────────────────────────────────────────

_DIVIDER = "─" * 28


def _privacy_icon(p: str) -> str:
    return {"public": "🌐", "unlisted": "🔗", "private": "🔒"}.get(p, "🔒")


async def _send_ready_message(update: Update, state: dict[str, Any], ai_fallback: bool) -> None:
    tags = state.get("tags", [])
    scheduled_at = state.get("scheduled_at")
    privacy = state.get("privacy", _default_privacy())
    wm = _watermark_text()

    schedule_line = (
        f"⏰ <b>Publish at:</b> <code>{_esc(scheduled_at)}</code>"
        if scheduled_at
        else "⚡ <b>Schedule:</b> Immediate upload"
    )
    ai_note = "\n⚠️ <i>AI unavailable — using original metadata</i>" if ai_fallback else ""
    tags_text = ", ".join(f"<code>{_esc(t)}</code>" for t in tags[:5]) if tags else "<i>none</i>"
    wm_text = f"<code>{_esc(wm)}</code>" if wm else "<i>none</i>"

    text = (
        f"✅ <b>Draft Ready</b>\n"
        f"{_DIVIDER}\n"
        f"📝 <b>Title:</b> {_esc(state.get('title', '')[:80])}\n"
        f"{_privacy_icon(privacy)} <b>Privacy:</b> {_esc(privacy.capitalize())}\n"
        f"{schedule_line}\n"
        f"🏷 <b>Tags:</b> {tags_text}\n"
        f"💧 <b>Watermark:</b> {wm_text}"
        f"{ai_note}\n"
        f"{_DIVIDER}\n"
        f"✏️ <b>Edit fields:</b>\n"
        f"  <code>/title New Title Here</code>\n"
        f"  <code>/desc New description</code>\n"
        f"  <code>/tags tag1, tag2, tag3</code>\n"
        f"  <code>/privacy public|unlisted|private</code>\n"
        f"  <code>/schedule 30m</code> · <code>2h</code> · <code>tomorrow9am</code>\n\n"
        f"💬 <b>Quick reply:</b> <code>1</code>=now · <code>2</code>=+1h · <code>3</code>=+6h · <code>4</code>=+24h · <code>5</code>=custom"
    )
    await update.effective_message.reply_text(
        text,
        parse_mode="HTML",
        reply_markup=_ready_actions_markup(),
    )


# ── Wizard ────────────────────────────────────────────────────────────────────

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
            "📅 <b>Custom Schedule</b>\n\n"
            "Send your desired publish time. Examples:\n"
            "  <code>30m</code>  →  30 minutes from now\n"
            "  <code>2h</code>   →  2 hours from now\n"
            "  <code>tomorrow9am</code>  →  tomorrow at 9 AM UTC\n"
            "  <code>2026-04-20T19:30:00Z</code>  →  exact UTC time",
            parse_mode="HTML",
        )
        return True
    if state.get("wizard_step") == "await_custom_schedule":
        try:
            parsed = _parse_schedule_input(choice)
        except ValueError:
            await update.effective_message.reply_text(
                "❌ <b>Couldn't parse that time.</b>\n\n"
                "Try: <code>30m</code>, <code>2h</code>, <code>tomorrow9am</code>, "
                "or <code>2026-04-15T19:30:00Z</code>",
                parse_mode="HTML",
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


# ── Text dispatcher ───────────────────────────────────────────────────────────

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
    if normalized in {"📊 stats", "stats"}:
        await update.effective_message.reply_text(
            "📊 <b>Analytics</b>\n\nUse <code>/stats &lt;history_id&gt;</code> to view stats for a specific upload.\n"
            "Run <code>/history</code> first to get the ID.",
            parse_mode="HTML",
        )
        return True
    if normalized in {"🗓 scheduled", "scheduled"}:
        await _scheduled(update, context)
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


# ── Callback query handler ────────────────────────────────────────────────────

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
    elif data == _ACTION_CANCEL:
        await _cancel(update, context)
    elif data == _ACTION_CLEAR_SCHEDULE:
        context.args = ["clear"]
        await _schedule(update, context)
    elif data == _ACTION_SCHEDULE_30M:
        context.args = ["30m"]
        await _schedule(update, context)
    elif data == _ACTION_SCHEDULE_1H:
        context.args = ["1h"]
        await _schedule(update, context)
    elif data == _ACTION_SCHEDULE_6H:
        context.args = ["6h"]
        await _schedule(update, context)
    elif data == _ACTION_SCHEDULE_24H:
        context.args = ["24h"]
        await _schedule(update, context)
    elif data == _ACTION_HISTORY:
        await _history(update, context)
    elif data == _ACTION_QUOTA:
        await _quota(update, context)
    elif data == _ACTION_SCHEDULED:
        await _scheduled(update, context)
    elif data == _ACTION_STATS:
        assert update.effective_message
        await update.effective_message.reply_text(
            "📊 <b>Analytics</b>\n\nUse <code>/stats &lt;history_id&gt;</code>.\n"
            "Run <code>/history</code> first to find the ID.",
            parse_mode="HTML",
        )


# ── AI metadata ───────────────────────────────────────────────────────────────

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


# ── URL handler (main flow) ───────────────────────────────────────────────────

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
            "💡 <b>Send a video URL to get started.</b>\n\n"
            f"Supported platforms: {_esc(SUPPORTED_MSG)}\n\n"
            "Or use the menu buttons below for quick actions.",
            parse_mode="HTML",
            reply_markup=_main_menu_markup(),
        )
        return

    url = text
    if _pending.get(chat_id):
        await update.effective_message.reply_text(
            "⚠️ <b>You already have a pending draft.</b>\n\n"
            "Reply <code>1</code> to upload now, <code>5</code> for custom schedule, "
            "or <b>❌ Cancel</b> to discard it first.",
            parse_mode="HTML",
        )
        return

    platform = detect_platform(url)
    if platform is None:
        await update.effective_message.reply_text(
            f"❌ <b>Unsupported URL</b>\n\n"
            f"Supported platforms:\n{_esc(SUPPORTED_MSG)}",
            parse_mode="HTML",
        )
        return

    # Step 1 — platform detected
    progress_msg = await update.effective_message.reply_text(
        f"🔍 <b>Detected:</b> <code>{_esc(platform.capitalize())}</code>\n"
        f"⬇️ Downloading video…",
        parse_mode="HTML",
    )

    # Step 2 — download
    async with _typing(update):
        try:
            result = await asyncio.to_thread(download_video, DownloadBody(url=url))
        except HTTPException as exc:
            await progress_msg.edit_text(
                f"❌ <b>Download failed</b>\n\n"
                f"{_esc(str(exc.detail))}\n\n"
                "💡 Check the URL and try again.",
                parse_mode="HTML",
            )
            return
        except Exception as exc:
            await progress_msg.edit_text(
                f"❌ <b>Download failed</b>\n\n"
                f"{_esc(str(exc))}\n\n"
                "💡 Check the URL and try again.",
                parse_mode="HTML",
            )
            return

    # Send thumbnail if available
    thumb_b64 = result.get("thumbnail", "")
    if thumb_b64:
        try:
            await update.effective_message.reply_photo(photo=BytesIO(base64.b64decode(thumb_b64)))
        except Exception:
            logger.exception("Failed sending thumbnail for chat %s", chat_id)

    # Step 3 — AI metadata
    await progress_msg.edit_text(
        f"📹 <b>{_esc(result.get('title', '')[:60])}</b>\n"
        f"🌐 <b>Platform:</b> {_esc(result.get('platform_label', platform))}\n"
        f"⏱ <b>Duration:</b> {int(float(result.get('duration') or 0))}s\n\n"
        f"✨ Generating AI metadata…",
        parse_mode="HTML",
    )

    original_title = result.get("title", "")
    original_desc = result.get("description", "")
    original_tags = result.get("tags", []) or []
    ai_fallback = False

    async with _typing(update):
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

    # Delete progress message, then send the ready card
    try:
        await progress_msg.delete()
    except Exception:
        pass

    await _send_ready_message(update, _pending[chat_id], ai_fallback)


# ── Command handlers ──────────────────────────────────────────────────────────

@_guard("start")
async def _start(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    await update.effective_message.reply_text(
        "👋 <b>Welcome to YT Automation Bot!</b>\n"
        f"{_DIVIDER}\n"
        "🚀 <b>Fastest flow:</b>\n"
        "  1️⃣  Send a reel / short URL\n"
        "  2️⃣  AI auto-generates title, description &amp; tags\n"
        "  3️⃣  Tap <b>Upload Now</b> or a schedule button\n\n"
        "✨ <b>Features:</b>\n"
        "  • Multi-platform (IG, TikTok, YT Shorts, Twitter…)\n"
        "  • AI-powered SEO metadata via Groq\n"
        "  • Flexible scheduling (+30m / +1h / +6h / +24h / custom)\n"
        "  • Quota tracker &amp; upload history\n\n"
        "Type <code>/help</code> for all commands or just send a URL to begin! 👇",
        parse_mode="HTML",
        reply_markup=_main_menu_markup(),
    )


@_guard("menu")
async def _menu(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_message
    await update.effective_message.reply_text(
        "📱 <b>Main Menu</b>\n\nAll actions are in the keyboard below.\nSend a URL to start a new draft.",
        parse_mode="HTML",
        reply_markup=_main_menu_markup(),
    )


@_guard("help")
async def _help(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_message
    await update.effective_message.reply_text(
        "ℹ️ <b>Command Reference</b>\n"
        f"{_DIVIDER}\n"
        "📤 <b>Uploading</b>\n"
        "  Send any supported URL → auto-download + AI metadata\n"
        "  <code>/upload</code>  — upload pending draft immediately\n"
        "  <code>/cancel</code>  — discard current draft\n\n"
        "✏️ <b>Edit Metadata</b>\n"
        "  <code>/title My New Title</code>\n"
        "  <code>/desc My description text</code>\n"
        "  <code>/tags tag1, tag2, tag3</code>\n"
        "  <code>/privacy public|unlisted|private</code>\n\n"
        "🗓 <b>Scheduling</b>\n"
        "  <code>/schedule 30m</code>  <code>/schedule 2h</code>  <code>/schedule tomorrow9am</code>\n"
        "  <code>/schedule 2026-04-20T19:30:00Z</code>  (exact UTC)\n"
        "  <code>/schedule clear</code>  — remove schedule\n"
        "  <code>/scheduled</code>  — view upcoming scheduled uploads\n\n"
        "📊 <b>Info</b>\n"
        "  <code>/status</code>  — current draft details\n"
        "  <code>/history</code>  — last 5 uploads\n"
        "  <code>/stats &lt;id&gt;</code>  — views, likes, comments\n"
        "  <code>/quota</code>  — YouTube API quota remaining\n"
        "  <code>/health</code>  — bot health dashboard\n"
        "  <code>/ping</code>  — latency check",
        parse_mode="HTML",
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
    last_error = _runtime.get("last_error") or ""
    restart_count = int(_runtime.get("restart_count", 0))

    bot_status = "✅ Running" if _app is not None else "❌ Stopped"
    error_status = (
        f"⚠️ <code>{_esc(last_error[:80])}</code> ({_fmt_age(last_error_at) if isinstance(last_error_at, datetime) else 'never'})"
        if last_error
        else "✅ None"
    )
    restarts_icon = "✅" if restart_count == 0 else "⚠️"

    await update.effective_message.reply_text(
        f"🩺 <b>Bot Health Dashboard</b>\n"
        f"{_DIVIDER}\n"
        f"🤖 <b>Status:</b> {bot_status}\n"
        f"⏱ <b>Uptime:</b> {_fmt_age(started_at) if isinstance(started_at, datetime) else 'n/a'}\n"
        f"📬 <b>Pending drafts:</b> {len(_pending)}\n"
        f"✔️ <b>Last command:</b> <code>{_esc(_runtime.get('last_command') or 'none')}</code>\n"
        f"   ({_fmt_age(last_ok) if isinstance(last_ok, datetime) else 'never'})\n"
        f"{restarts_icon} <b>Restarts:</b> {restart_count}\n"
        f"🔴 <b>Last error:</b> {error_status}",
        parse_mode="HTML",
    )


@_guard("ping")
async def _ping(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_message
    start = time.perf_counter()
    msg = await update.effective_message.reply_text("🏓 Pinging…")
    latency_ms = int((time.perf_counter() - start) * 1000)
    await msg.edit_text(f"🏓 <b>Pong!</b>  <code>{latency_ms}ms</code>", parse_mode="HTML")


@_guard("status")
async def _status(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_chat and update.effective_message
    state = _pending.get(update.effective_chat.id)
    if not state:
        await update.effective_message.reply_text(
            "📭 <b>No pending draft.</b>\n\nSend a URL to create one.",
            parse_mode="HTML",
            reply_markup=_main_menu_markup(),
        )
        return
    tags = state.get("tags", [])
    privacy = state.get("privacy", _default_privacy())
    scheduled_at = state.get("scheduled_at")
    tags_text = ", ".join(f"<code>{_esc(t)}</code>" for t in tags[:5]) if tags else "<i>none</i>"
    schedule_text = (
        f"<code>{_esc(scheduled_at)}</code>" if scheduled_at else "<i>Immediate (no schedule)</i>"
    )
    await update.effective_message.reply_text(
        f"📋 <b>Pending Draft</b>\n"
        f"{_DIVIDER}\n"
        f"🆔 <b>Video ID:</b> <code>{_esc(state.get('video_id', ''))}</code>\n"
        f"📝 <b>Title:</b> {_esc(state.get('title', '')[:70])}\n"
        f"{_privacy_icon(privacy)} <b>Privacy:</b> {_esc(privacy.capitalize())}\n"
        f"🗓 <b>Schedule:</b> {schedule_text}\n"
        f"🏷 <b>Tags:</b> {tags_text}",
        parse_mode="HTML",
        reply_markup=_ready_actions_markup(),
    )


@_guard("pending")
async def _pending_cmd(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_message
    if not _pending:
        await update.effective_message.reply_text(
            "📭 <b>No pending uploads.</b>", parse_mode="HTML"
        )
        return
    lines = [f"📬 <b>Pending uploads: {len(_pending)}</b>\n{_DIVIDER}"]
    for chat_id, state in list(_pending.items())[:10]:
        lines.append(
            f"• Chat <code>{chat_id}</code>: {_esc(state.get('title', 'Untitled')[:40])}\n"
            f"  Schedule: <code>{state.get('scheduled_at') or 'none'}</code>"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


@_guard("schedule")
async def _schedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_chat and update.effective_message
    state = _pending.get(update.effective_chat.id)
    if not state:
        await update.effective_message.reply_text(
            "📭 <b>No pending draft.</b>\n\nSend a URL first.", parse_mode="HTML"
        )
        return
    raw = " ".join(context.args).strip()
    if not raw:
        await update.effective_message.reply_text(
            "🗓 <b>Schedule Usage</b>\n\n"
            "<code>/schedule 30m</code>  <code>/schedule 2h</code>  <code>/schedule tomorrow9am</code>\n"
            "<code>/schedule 2026-04-20T19:30:00Z</code>  (exact UTC)\n"
            "<code>/schedule clear</code>  — remove schedule",
            parse_mode="HTML",
        )
        return
    if raw.lower() in {"none", "clear", "off"}:
        state["scheduled_at"] = None
        await update.effective_message.reply_text(
            "🗑 <b>Schedule cleared.</b> This draft will upload immediately.",
            parse_mode="HTML",
        )
        await _send_ready_message(update, state, False)
        return
    try:
        parsed = _parse_schedule_input(raw)
    except ValueError as exc:
        await update.effective_message.reply_text(
            f"❌ <b>Invalid schedule time</b>\n\n{_esc(str(exc))}\n\n"
            "Try: <code>30m</code>, <code>2h</code>, <code>tomorrow9am</code>, or ISO UTC datetime.",
            parse_mode="HTML",
        )
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
        await update.effective_message.reply_text(
            "📭 <b>No scheduled uploads found</b> in recent history.",
            parse_mode="HTML",
        )
        return
    lines = [f"🗓 <b>Upcoming Scheduled Uploads</b>\n{_DIVIDER}"]
    for row in scheduled_rows[:10]:
        link = row.get("youtube_url") or "no link yet"
        link_text = f'<a href="{_esc(link)}">View ↗</a>' if link.startswith("http") else "<i>pending</i>"
        lines.append(
            f"• <b>#{row['id']}</b>  {_esc(row.get('scheduled_at') or 'n/a')}\n"
            f"  Status: <code>{_esc(row.get('status', 'unknown'))}</code>  {link_text}"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


@_guard("unschedule")
async def _unschedule(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_message
    await update.effective_message.reply_text(
        "⚠️ <b>Unschedule not yet supported.</b>\n\n"
        "To cancel a scheduled video, delete it from YouTube Studio directly,\n"
        "or use <code>/schedule clear</code> before uploading.",
        parse_mode="HTML",
    )


@_guard("cancel")
async def _cancel(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_chat and update.effective_message
    had_pending = _pending.pop(update.effective_chat.id, None)
    if had_pending:
        await update.effective_message.reply_text(
            "🗑 <b>Draft discarded.</b>\n\nSend a new URL whenever you're ready.",
            parse_mode="HTML",
            reply_markup=_main_menu_markup(),
        )
    else:
        await update.effective_message.reply_text(
            "📭 Nothing to cancel — no pending draft.",
            parse_mode="HTML",
            reply_markup=_main_menu_markup(),
        )


@_guard("upload")
async def _upload(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_chat and update.effective_message
    chat_id = update.effective_chat.id
    state = _pending.get(chat_id)
    if not state:
        await update.effective_message.reply_text(
            "📭 <b>No pending draft.</b>\n\nSend a URL first.",
            parse_mode="HTML",
        )
        return

    progress_msg = await update.effective_message.reply_text(
        "⬆️ <b>Uploading to YouTube…</b>\n⏳ This may take a moment.",
        parse_mode="HTML",
    )

    video_id = state["video_id"]
    wm = _watermark_text()
    if wm:
        try:
            async with _typing(update):
                edit_res = await asyncio.to_thread(
                    edit_video, EditBody(video_id=video_id, watermark_text=wm)
                )
            video_id = edit_res["edited_video_id"]
        except Exception:
            logger.exception("Watermark apply failed for chat %s; continuing with original video", chat_id)

    async with _typing(update):
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
            if exc.status_code == 429:
                await progress_msg.edit_text(
                    "❌ <b>YouTube Quota Exhausted</b>\n\n"
                    "💡 Quota resets at midnight UTC. Try again after that.",
                    parse_mode="HTML",
                )
            elif exc.status_code == 401:
                await progress_msg.edit_text(
                    "❌ <b>Authentication Error</b>\n\n"
                    "💡 Your YouTube token may be expired. Re-authenticate via the web UI.",
                    parse_mode="HTML",
                )
            else:
                await progress_msg.edit_text(
                    f"❌ <b>Upload Failed</b>\n\n{_esc(str(exc.detail))}",
                    parse_mode="HTML",
                )
            return
        except Exception as exc:
            await progress_msg.edit_text(
                f"❌ <b>Upload Failed</b>\n\n{_esc(str(exc))}",
                parse_mode="HTML",
            )
            return

    youtube_url = result.get("youtube_url", "")
    history_id = _resolve_history_id(youtube_url)
    history_text = f"{history_id}" if history_id else "latest"
    _pending.pop(chat_id, None)

    try:
        await progress_msg.delete()
    except Exception:
        pass

    if state.get("scheduled_at"):
        await update.effective_message.reply_text(
            f"🗓 <b>Scheduled Successfully!</b>\n"
            f"{_DIVIDER}\n"
            f"📺 <a href=\"{_esc(youtube_url)}\">Open on YouTube</a>\n"
            f"⏰ <b>Publishes at:</b> <code>{_esc(state.get('scheduled_at', ''))}</code>\n\n"
            f"📊 Check stats after publish with:\n<code>/stats {history_text}</code>",
            parse_mode="HTML",
            reply_markup=_main_menu_markup(),
        )
    else:
        await update.effective_message.reply_text(
            f"🎉 <b>Uploaded Successfully!</b>\n"
            f"{_DIVIDER}\n"
            f"📺 <a href=\"{_esc(youtube_url)}\">Open on YouTube</a>\n\n"
            f"📊 Check analytics in ~48h with:\n<code>/stats {history_text}</code>",
            parse_mode="HTML",
            reply_markup=_main_menu_markup(),
        )


# ── Field editors ─────────────────────────────────────────────────────────────

async def _set_field(update: Update, field: str, value: str) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_chat and update.effective_message
    state = _pending.get(update.effective_chat.id)
    if not state:
        await update.effective_message.reply_text(
            "📭 <b>No pending draft.</b>\n\nSend a URL first.", parse_mode="HTML"
        )
        return
    if not value.strip():
        await update.effective_message.reply_text(
            "⚠️ Please provide a value.", parse_mode="HTML"
        )
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
        await update.effective_message.reply_text(
            "📭 <b>No pending draft.</b>\n\nSend a URL first.", parse_mode="HTML"
        )
        return
    raw = " ".join(context.args).strip()
    tags = [t.strip().lstrip("#") for t in raw.split(",") if t.strip()][:30]
    if not tags:
        await update.effective_message.reply_text(
            "⚠️ Provide comma-separated tags.\nExample: <code>/tags gaming, funny, shorts</code>",
            parse_mode="HTML",
        )
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
        await update.effective_message.reply_text(
            "📭 <b>No pending draft.</b>\n\nSend a URL first.", parse_mode="HTML"
        )
        return
    value = (context.args[0] if context.args else "").strip().lower()
    if value not in {"public", "unlisted", "private"}:
        await update.effective_message.reply_text(
            "⚠️ <b>Usage:</b> <code>/privacy public|unlisted|private</code>",
            parse_mode="HTML",
        )
        return
    state["privacy"] = value
    await _send_ready_message(update, state, False)


# ── Info commands ─────────────────────────────────────────────────────────────

@_guard("quota")
async def _quota(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_message
    async with _typing(update):
        used = await asyncio.to_thread(get_quota_used, _default_account())
    total = 10_000
    remaining = max(0, total - used)
    uploads_left = remaining // 1600
    bar = _build_quota_bar(used, total)
    level_icon = "✅" if used < 7000 else ("⚠️" if used < 9500 else "🔴")
    await update.effective_message.reply_text(
        f"🔋 <b>YouTube API Quota</b>\n"
        f"{_DIVIDER}\n"
        f"{level_icon} <code>{bar}</code>\n"
        f"  Used: <b>{used:,}</b> / {total:,} units\n"
        f"  Remaining: ~<b>{uploads_left}</b> upload{'s' if uploads_left != 1 else ''}\n\n"
        f"💡 Quota resets at <b>midnight UTC</b> daily.",
        parse_mode="HTML",
    )


@_guard("history")
async def _history(update: Update, _: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_message
    async with _typing(update):
        rows = await asyncio.to_thread(get_history, 5, 0)
    if not rows:
        await update.effective_message.reply_text(
            "📭 <b>No upload history yet.</b>", parse_mode="HTML"
        )
        return

    status_icons = {
        "done": "✅", "uploaded": "✅", "scheduled": "🗓",
        "failed": "❌", "processing": "⏳",
    }
    lines = [f"📚 <b>Last 5 Uploads</b>\n{_DIVIDER}"]
    for row in rows:
        status = row.get("status", "unknown")
        icon = status_icons.get(status, "•")
        link = row.get("youtube_url") or ""
        link_text = (
            f'<a href="{_esc(link)}">Watch ↗</a>'
            if link.startswith("http")
            else "<i>no link</i>"
        )
        lines.append(
            f"{icon} <b>#{row['id']}</b>  <code>{_esc(status)}</code>\n"
            f"   {link_text}\n"
            f"   <i>Analytics: <code>/stats {row['id']}</code></i>"
        )
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


@_guard("stats")
async def _stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not await _require_authorized(update):
        return
    assert update.effective_message
    if not context.args:
        await update.effective_message.reply_text(
            "📊 <b>Usage:</b> <code>/stats &lt;history_id&gt;</code>\n\n"
            "Run <code>/history</code> to find the ID.",
            parse_mode="HTML",
        )
        return
    try:
        history_id = int(context.args[0])
    except ValueError:
        await update.effective_message.reply_text(
            "❌ <code>history_id</code> must be a number.", parse_mode="HTML"
        )
        return

    progress_msg = await update.effective_message.reply_text(
        "📊 <b>Fetching analytics…</b>", parse_mode="HTML"
    )
    async with _typing(update):
        try:
            stats = await asyncio.to_thread(get_analytics, history_id)
        except HTTPException as exc:
            await progress_msg.edit_text(
                f"❌ <b>Stats unavailable</b>\n\n{_esc(str(exc.detail))}",
                parse_mode="HTML",
            )
            return

    await progress_msg.edit_text(
        f"📊 <b>Analytics</b>\n"
        f"{_DIVIDER}\n"
        f"🎬 <b>{_esc(stats.get('title', ''))}</b>\n\n"
        f"👁 <b>Views:</b>    {stats.get('views', 0):,}\n"
        f"👍 <b>Likes:</b>    {stats.get('likes', 0):,}\n"
        f"💬 <b>Comments:</b> {stats.get('comments', 0):,}",
        parse_mode="HTML",
    )


# ── Error handler ─────────────────────────────────────────────────────────────

async def _handle_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("Telegram bot error: %s", context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                f"❌ <b>Something went wrong.</b>\n\n<code>{_esc(str(context.error))}</code>",
                parse_mode="HTML",
            )
        except Exception:
            logger.exception("Failed sending error back to Telegram user")


# ── Bot lifecycle ─────────────────────────────────────────────────────────────

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
