import json
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Literal
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

BACKEND_DIR = Path(__file__).resolve().parent
TMP_DIR = BACKEND_DIR / "tmp"
COOKIES_PATH = BACKEND_DIR / "cookies.txt"
CLIENT_SECRETS_PATH = BACKEND_DIR / "client_secrets.json"
QUOTA_PATH = BACKEND_DIR / "quota.json"

YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
OAUTH_PENDING_TTL_SEC = 15 * 60
_pending_oauth_flows: dict[str, dict[str, str | float]] = {}

# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

_PLATFORM_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("instagram", re.compile(r"^https?://(?:www\.)?instagram\.com/(?:reel|reels|p|tv)/[\w-]+", re.IGNORECASE)),
    ("snapchat",  re.compile(r"^https?://(?:www\.)?snapchat\.com/spotlight/[\w-]+", re.IGNORECASE)),
    ("tiktok",    re.compile(r"^https?://(?:www\.)?(?:tiktok\.com|vm\.tiktok\.com)/", re.IGNORECASE)),
    ("youtube",   re.compile(r"^https?://(?:www\.)?(?:youtube\.com/shorts/|youtu\.be/)[\w-]+", re.IGNORECASE)),
    ("twitter",   re.compile(r"^https?://(?:www\.)?(?:twitter\.com|x\.com)/\w+/status/\d+", re.IGNORECASE)),
    ("reddit",    re.compile(r"^https?://(?:www\.)?reddit\.com/r/\w+/comments/", re.IGNORECASE)),
    ("pinterest", re.compile(r"^https?://(?:www\.)?pinterest\.com/pin/", re.IGNORECASE)),
]

Platform = Literal["instagram", "snapchat", "tiktok", "youtube", "twitter", "reddit", "pinterest"]

PLATFORM_LABELS: dict[str, str] = {
    "instagram": "Instagram",
    "snapchat":  "Snapchat",
    "tiktok":    "TikTok",
    "youtube":   "YouTube Shorts",
    "twitter":   "Twitter/X",
    "reddit":    "Reddit",
    "pinterest": "Pinterest",
}


def detect_platform(url: str) -> Platform | None:
    for name, pattern in _PLATFORM_PATTERNS:
        if pattern.match(url):
            return name  # type: ignore[return-value]
    return None


def needs_cookies(platform: Platform) -> bool:
    """Only Instagram requires a cookies file; all other platforms are public."""
    return platform == "instagram"


# ---------------------------------------------------------------------------
# File helpers
# ---------------------------------------------------------------------------

def cleanup_old_tmp_files(max_age_sec: int = 3600) -> None:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for path in TMP_DIR.iterdir():
        if not path.is_file():
            continue
        try:
            if now - path.stat().st_mtime > max_age_sec:
                path.unlink(missing_ok=True)
        except OSError:
            pass


def find_video_path(video_id: str) -> Path:
    skip_exts = {".jpg", ".jpeg", ".png", ".webp", ".srt", ".wav"}
    matches = [p for p in TMP_DIR.glob(f"{video_id}.*") if p.suffix.lower() not in skip_exts]
    if not matches:
        raise HTTPException(status_code=404, detail="Video not found or expired. Download again.")
    return matches[0]


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------

def run_ffmpeg(args: list[str]) -> None:
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", *args],
            capture_output=True,
            text=True,
            timeout=7200,
        )
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="ffmpeg not found. Install ffmpeg and add it to PATH.")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=500, detail="Video processing timed out.")
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()[-2000:]
        raise HTTPException(status_code=500, detail=f"ffmpeg failed: {err or 'unknown error'}")


def escape_drawtext(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
    )


def hex_to_ffmpeg_color(color: str) -> str:
    c = color.strip().lstrip("#")
    if len(c) == 6 and all(ch in "0123456789abcdefABCDEF" for ch in c):
        return f"0x{c}FF"
    if len(c) == 8 and all(ch in "0123456789abcdefABCDEF" for ch in c):
        return f"0x{c}"
    raise HTTPException(status_code=400, detail="Invalid color. Use #RRGGBB or #RRGGBBAA.")


def overlay_xy(position: str) -> str:
    positions = {
        "top":          "x=(w-text_w)/2:y=50",
        "bottom":       "x=(w-text_w)/2:y=h-text_h-50",
        "center":       "x=(w-text_w)/2:y=(h-text_h)/2",
        "top-left":     "x=30:y=30",
        "top-right":    "x=w-text_w-30:y=30",
        "bottom-left":  "x=30:y=h-text_h-30",
        "bottom-right": "x=w-text_w-30:y=h-text_h-30",
    }
    return positions.get(position, "x=(w-text_w)/2:y=h-text_h-50")


# ---------------------------------------------------------------------------
# YouTube service
# ---------------------------------------------------------------------------

def get_token_path(account: str) -> Path:
    if account == "default" and (BACKEND_DIR / "token.json").is_file():
        return BACKEND_DIR / "token.json"
    return BACKEND_DIR / f"token_{account}.json"


def authenticate_youtube_account(account: str):
    from google_auth_oauthlib.flow import InstalledAppFlow
    
    if not CLIENT_SECRETS_PATH.is_file():
        raise HTTPException(
            status_code=503,
            detail="YouTube OAuth not configured. Add client_secrets.json (see README).",
        )
        
    try:
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_PATH), YOUTUBE_SCOPES)
        creds = flow.run_local_server(port=0)
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"OAuth setup failed: {exc}") from exc
        
    token_path = get_token_path(account)
    token_path.write_text(creds.to_json(), encoding="utf-8")


def _cleanup_pending_oauth_flows() -> None:
    now = time.time()
    expired = [
        state
        for state, pending in _pending_oauth_flows.items()
        if now - float(pending.get("created_at", 0)) > OAUTH_PENDING_TTL_SEC
    ]
    for state in expired:
        _pending_oauth_flows.pop(state, None)


def _extract_oauth_code_and_state(value: str) -> tuple[str, str | None]:
    raw = value.strip()
    if "://" not in raw and "code=" not in raw and "state=" not in raw:
        return raw, None

    parsed = urlparse(raw)
    query = parse_qs(parsed.query)
    code = (query.get("code") or [""])[0].strip()
    state = (query.get("state") or [None])[0]
    return code or raw, state


def get_oauth_auth_url(account: str, redirect_uri: str) -> str:
    from google_auth_oauthlib.flow import InstalledAppFlow
    if not CLIENT_SECRETS_PATH.is_file():
        raise HTTPException(
            status_code=503,
            detail="YouTube OAuth not configured. Add client_secrets.json (see README).",
        )

    _cleanup_pending_oauth_flows()
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_PATH), YOUTUBE_SCOPES)
    flow.redirect_uri = redirect_uri
    auth_url, state = flow.authorization_url(prompt="consent", include_granted_scopes="true")
    _pending_oauth_flows[state] = {
        "account": account,
        "redirect_uri": redirect_uri,
        "code_verifier": flow.code_verifier or "",
        "created_at": time.time(),
    }
    return auth_url


def exchange_oauth_code(account: str, code: str) -> str:
    from google_auth_oauthlib.flow import InstalledAppFlow

    _cleanup_pending_oauth_flows()
    code_value, state = _extract_oauth_code_and_state(code)

    pending_state = state
    pending = _pending_oauth_flows.get(pending_state) if pending_state else None
    if pending is None and account.strip():
        matches = [
            (pending_key, pending_value)
            for pending_key, pending_value in _pending_oauth_flows.items()
            if pending_value.get("account") == account.strip()
        ]
        if matches:
            pending_state, pending = max(matches, key=lambda item: float(item[1].get("created_at", 0)))

    if pending is None or pending_state is None:
        raise HTTPException(status_code=400, detail="OAuth session expired. Start sign-in again.")

    resolved_account = account.strip() or str(pending["account"])
    if resolved_account != pending["account"]:
        raise HTTPException(status_code=400, detail="OAuth session does not match the selected account.")

    flow = InstalledAppFlow.from_client_secrets_file(
        str(CLIENT_SECRETS_PATH),
        YOUTUBE_SCOPES,
        state=pending_state,
        code_verifier=str(pending["code_verifier"]),
    )
    flow.redirect_uri = str(pending["redirect_uri"])
    try:
        flow.fetch_token(code=code_value)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid code or auth failed: {exc}") from exc
    creds = flow.credentials
    token_path = get_token_path(resolved_account)
    token_path.write_text(creds.to_json(), encoding="utf-8")
    _pending_oauth_flows.pop(pending_state, None)
    return resolved_account


def get_youtube_service(account: str = "default"):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    if not CLIENT_SECRETS_PATH.is_file():
        raise HTTPException(
            status_code=503,
            detail="YouTube OAuth not configured. Add client_secrets.json (see README).",
        )
    creds = None
    token_path = get_token_path(account)
    
    if token_path.is_file():
        creds = Credentials.from_authorized_user_file(str(token_path), YOUTUBE_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"Could not refresh YouTube token: {exc}") from exc
        else:
            raise HTTPException(status_code=401, detail="YouTube OAuth token missing or expired. Please re-authenticate.")
        token_path.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


# ---------------------------------------------------------------------------
# Quota Tracking
# ---------------------------------------------------------------------------

def _load_quota() -> dict:
    if not QUOTA_PATH.is_file():
        return {}
    try:
        return json.loads(QUOTA_PATH.read_text("utf-8"))
    except:
        return {}


def _save_quota(data: dict) -> None:
    QUOTA_PATH.write_text(json.dumps(data), "utf-8")


def get_quota_used(account: str) -> int:
    """Return units used today for this account."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    data = _load_quota()
    acc_data = data.get(account, {})
    if acc_data.get("date") != today:
        return 0
    return acc_data.get("used", 0)


def increment_quota(account: str, units: int) -> None:
    """Add units to today's count. Reset if date changed."""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    data = _load_quota()
    acc_data = data.get(account, {})
    if acc_data.get("date") != today:
        acc_data = {"date": today, "used": 0}
    acc_data["used"] += units
    data[account] = acc_data
    _save_quota(data)


def check_quota(account: str, units_needed: int = 1600) -> None:
    """Raise HTTPException 429 if quota would be exceeded."""
    used = get_quota_used(account)
    if used + units_needed > 10000:
        raise HTTPException(
            status_code=429,
            detail=f"YouTube API quota exceeded for today. Used {used}/10000. Try again tomorrow."
        )
