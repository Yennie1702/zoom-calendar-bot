"""Config loader — single source of truth for env vars."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

log = logging.getLogger(__name__)


def _required(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise RuntimeError(f"Missing required env var: {name}. Check .env file.")
    return val


def _optional(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


# Telegram
TELEGRAM_BOT_TOKEN = _required("TELEGRAM_BOT_TOKEN")
TELEGRAM_ALLOWED_CHAT_ID = int(_required("TELEGRAM_ALLOWED_CHAT_ID"))

# Zoom S2S OAuth
ZOOM_ACCOUNT_ID = _required("ZOOM_ACCOUNT_ID")
ZOOM_CLIENT_ID = _required("ZOOM_CLIENT_ID")
ZOOM_CLIENT_SECRET = _required("ZOOM_CLIENT_SECRET")

# Google Calendar (required for Phase 2 standalone bot)
GOOGLE_CLIENT_ID = _optional("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = _optional("GOOGLE_CLIENT_SECRET")
GOOGLE_REFRESH_TOKEN = _optional("GOOGLE_REFRESH_TOKEN")
GOOGLE_CALENDAR_ACCOUNT = _optional("GOOGLE_CALENDAR_ACCOUNT", "primary")

# Turso libSQL (optional — if both set, bot/db.py dùng Turso remote;
# nếu trống → fallback SQLite file local data/events.db)
TURSO_DATABASE_URL = _optional("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = _optional("TURSO_AUTH_TOKEN")

# Template (non-secret, but user-specific)
CONTACT_NAME = _optional("CONTACT_NAME", "Hai Yen")
CONTACT_TITLE = _optional("CONTACT_TITLE", "PM du an - John Academy")

# Project constants (not user-configurable)
TIMEZONE = "Asia/Ho_Chi_Minh"
LOG_LEVEL = _optional("LOG_LEVEL", "INFO")


def google_ready() -> bool:
    """True when Google OAuth credentials are populated."""
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GOOGLE_REFRESH_TOKEN)


def setup_logging() -> None:
    logging.basicConfig(
        level=getattr(logging, LOG_LEVEL, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # Dampen noisy libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)
