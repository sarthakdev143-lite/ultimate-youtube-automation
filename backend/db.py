"""SQLite persistence layer — history, scheduled uploads, and presets."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator

DB_PATH = Path(__file__).resolve().parent / "factory.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

def init_db() -> None:
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id     TEXT    NOT NULL,
                source_url   TEXT    DEFAULT '',
                platform     TEXT    DEFAULT '',
                title        TEXT    DEFAULT '',
                youtube_url  TEXT,
                status       TEXT    NOT NULL DEFAULT 'uploaded',
                scheduled_at TEXT,
                created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS presets (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT    NOT NULL UNIQUE,
                settings_json TEXT    NOT NULL,
                created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
            )
        """)


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def insert_history(
    video_id: str,
    source_url: str = "",
    platform: str = "",
    title: str = "",
    youtube_url: str | None = None,
    status: str = "uploaded",
    scheduled_at: str | None = None,
) -> int:
    with get_db() as conn:
        cur = conn.execute(
            """INSERT INTO history
               (video_id, source_url, platform, title, youtube_url, status, scheduled_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (video_id, source_url, platform, title, youtube_url, status, scheduled_at),
        )
        return cur.lastrowid  # type: ignore[return-value]


def update_history_status(history_id: int, status: str, youtube_url: str | None = None) -> None:
    with get_db() as conn:
        if youtube_url:
            conn.execute(
                "UPDATE history SET status=?, youtube_url=? WHERE id=?",
                (status, youtube_url, history_id),
            )
        else:
            conn.execute("UPDATE history SET status=? WHERE id=?", (status, history_id))


def get_history(limit: int = 50, offset: int = 0) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM history ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(r) for r in rows]


def get_history_by_id(history_id: int) -> dict | None:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM history WHERE id=?", (history_id,)).fetchone()
        return dict(row) if row else None


def delete_history(history_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM history WHERE id=?", (history_id,))
        return cur.rowcount > 0


def get_scheduled_pending() -> list[dict]:
    """Return scheduled rows whose scheduled_at time has passed."""
    with get_db() as conn:
        rows = conn.execute(
            """SELECT * FROM history
               WHERE status = 'scheduled'
                 AND scheduled_at IS NOT NULL
                 AND datetime(scheduled_at) <= datetime('now')""",
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------

def save_preset(name: str, settings: dict) -> None:
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO presets (name, settings_json) VALUES (?, ?)",
            (name, json.dumps(settings)),
        )


def get_presets() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, settings_json, created_at FROM presets ORDER BY created_at DESC"
        ).fetchall()
        return [
            {"id": r["id"], "name": r["name"], "settings": json.loads(r["settings_json"]), "created_at": r["created_at"]}
            for r in rows
        ]


def delete_preset(preset_id: int) -> bool:
    with get_db() as conn:
        cur = conn.execute("DELETE FROM presets WHERE id=?", (preset_id,))
        return cur.rowcount > 0
