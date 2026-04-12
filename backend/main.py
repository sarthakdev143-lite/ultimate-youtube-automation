"""Application entry point — wires routers, CORS, scheduler, and DB init."""
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from db import init_db
from scheduler import start_scheduler, stop_scheduler
from routers import ai, analytics, batch, download, edit, history, upload


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


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
