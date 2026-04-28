"""External digest trigger — chạy độc lập trên GitHub Actions, KHÔNG cần
bot Render alive.

Workflow gọi script này 1 lần lúc 7:00 sáng VN (= 00:00 UTC). Script đọc DB
Turso + Google Calendar trực tiếp, render digest, gửi qua Telegram Bot API.

Idempotent: nếu `bot_meta.last_digest_date` đã = today → skip (tránh duplicate
nếu cả internal scheduler lẫn workflow này cùng fire trong ngày).

Required env: TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_CHAT_ID, TURSO_DATABASE_URL,
TURSO_AUTH_TOKEN, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN,
GOOGLE_CALENDAR_ACCOUNT.
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bot import config, db, external_events, scheduler  # noqa: E402


def main() -> int:
    now = scheduler._now_vn()
    today = now.date().isoformat()
    print(f"VN now: {now.isoformat()}")

    if db.get_meta("last_digest_date") == today:
        print(f"⏭  Digest cho {today} đã fire (bởi Render bot hoặc workflow trước). Skip.")
        return 0

    items = db.events_on_date(today)
    try:
        externals = external_events.fetch_on_date(now.date())
    except Exception:
        print("⚠️  fetch external fail — gửi digest chỉ với DB events")
        externals = []

    text = scheduler._format_digest(today, items, externals)
    api = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(
        api,
        json={
            "chat_id": config.TELEGRAM_ALLOWED_CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    r.raise_for_status()
    db.set_meta("last_digest_date", today)
    print(f"✅ Digest sent ({len(items)} bot lịch + {len(externals)} external) for {today}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
