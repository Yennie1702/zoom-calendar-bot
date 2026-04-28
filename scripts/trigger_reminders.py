"""External reminder trigger — chạy mỗi 5 phút trên GitHub Actions.

Check window 28-32 phút trước mỗi event sắp tới (cả lịch DB + Calendar). Nếu
event nào vào window và chưa được mark reminded → gửi tin nhắn nhắc qua
Telegram Bot API.

Idempotent qua `events.reminders_sent` (DB) và `external_reminders_sent` (DB)
— giống logic của internal scheduler.

KHÔNG cần Render bot alive. Workflow chạy mỗi 5 phút sẽ cover toàn bộ window
28-32 phút (= 4 phút) — kể cả nếu 1-2 lần ping bị trễ vẫn sẽ có ít nhất 1 lần
ping rơi vào window.

Required env: TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_CHAT_ID, TURSO_*,
GOOGLE_*.
"""
from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bot import config, db, external_events, scheduler, formatter  # noqa: E402


def _send(text: str) -> None:
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


def main() -> int:
    now = scheduler._now_vn()
    # Window expand một chút (25-35 phút) để cover GitHub Actions cron jitter
    # — workflow có thể trễ vài phút, cần buffer rộng hơn 4-phút window
    # mặc định của internal scheduler.
    LEAD_MIN = scheduler.REMINDER_LEAD_MIN  # 30
    HALF_WIN = max(scheduler.REMINDER_HALF_WINDOW_MIN, 5)  # ≥ 5 phút each side

    lower = now + timedelta(minutes=LEAD_MIN - HALF_WIN)
    upper = now + timedelta(minutes=LEAD_MIN + HALF_WIN)
    print(f"Now: {now.isoformat()} | Window: {lower.time()} → {upper.time()}")

    sent_count = 0

    # 1) Bot-created lịch
    for t in db.upcoming_unreminded(lower, upper):
        row = t["row"]
        occ_iso = t["occurrence_iso"]
        text = scheduler._format_reminder(row, occ_iso)
        try:
            _send(text)
            db.mark_reminded(row.id, occ_iso)
            print(f"✅ Bot reminder: id={row.id} occ={occ_iso}")
            sent_count += 1
        except Exception as e:
            print(f"❌ Bot reminder fail (id={row.id}): {e}")

    # 2) External lịch (Calendar không do bot tạo)
    try:
        ext_occs = external_events.fetch_in_datetime_window(lower, upper)
    except Exception as e:
        print(f"⚠️  fetch external fail: {e}")
        ext_occs = []

    for occ in ext_occs:
        if db.is_external_reminded(occ.calendar_event_id, occ.occurrence_iso):
            continue
        text = scheduler._format_external_reminder(occ)
        try:
            _send(text)
            db.mark_external_reminded(occ.calendar_event_id, occ.occurrence_iso)
            print(f"✅ External reminder: {occ.topic} occ={occ.occurrence_iso}")
            sent_count += 1
        except Exception as e:
            print(f"❌ External reminder fail ({occ.topic}): {e}")

    print(f"Total reminders sent: {sent_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
