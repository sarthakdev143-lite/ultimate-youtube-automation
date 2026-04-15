"""Application entry point — wires routers, CORS, scheduler, and DB init."""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from db import init_db
from routers import ai, analytics, batch, download, edit, history, upload
from telegram_bot import (
    get_telegram_runtime_status,
    process_webhook_update,
    start_telegram_bot,
    start_telegram_bot_webhook,
    stop_telegram_bot,
)

logger = logging.getLogger(__name__)


def _resolve_webhook_url() -> str | None:
    """Return the full webhook URL if we're running in a hosted environment.

    Render automatically injects RENDER_EXTERNAL_URL.
    You can also set WEBHOOK_URL manually for other platforms.
    """
    # Explicit override takes priority
    explicit = os.getenv("WEBHOOK_URL", "").strip()
    if explicit:
        return explicit.rstrip("/") + "/webhook/telegram"

    # Render sets this automatically for every web service
    render_url = os.getenv("RENDER_EXTERNAL_URL", "").strip()
    if render_url:
        return render_url.rstrip("/") + "/webhook/telegram"

    return None


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    webhook_url = _resolve_webhook_url()
    if webhook_url:
        logger.info("Starting Telegram bot in WEBHOOK mode: %s", webhook_url)
        await start_telegram_bot_webhook(webhook_url)
    else:
        logger.info("Starting Telegram bot in POLLING mode (local dev)")
        await start_telegram_bot()
    yield
    await stop_telegram_bot()


app = FastAPI(title="YT Automation Factory API", version="2.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(download.router)
app.include_router(edit.router)
app.include_router(upload.router)
app.include_router(history.router)
app.include_router(ai.router)
app.include_router(batch.router)
app.include_router(analytics.router)


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    return JSONResponse(status_code=exc.status_code, content={"detail": detail})


@app.get("/health")
async def health():
    return {"ok": True, "telegram": get_telegram_runtime_status()}


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Receive Telegram updates via webhook (used in production on Render)."""
    data = await request.json()
    await process_webhook_update(data)
    return {"ok": True}
