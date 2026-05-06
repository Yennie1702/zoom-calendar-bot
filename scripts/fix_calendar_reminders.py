"""One-shot fix: patch reminders của TẤT CẢ active events → popup-only.

Chuyện gì đã xảy ra
-------------------
- Trước commit `9cad917` (2/5), bot tạo Calendar event với:
  reminders.overrides = [{method=email, minutes=30}, {method=popup, minutes=1440}]
  → mỗi lần reminder fire, Google gửi email "Lịch Google: Thông báo..."
  vào Gmail của chị Yến (và mọi attendee).
- Commit `9cad917` đã đổi code create_event → chỉ popup. Lịch tạo SAU commit
  này KHÔNG còn email reminder.
- Lịch tạo TRƯỚC commit này (vd recurring series 8 buổi tháng 4) → embed
  email reminder trong Calendar event → Google API không tự re-apply default,
  vẫn fire email cho mỗi instance còn lại.

Script này
----------
- Query tất cả active events trong DB có `calendar_event_id`
- Patch reminders → popup-only (giữ nguyên content/time/attendees khác)
- Dùng `sendUpdates="none"` để KHÔNG spam khách email "lịch đã update"
- Idempotent: chạy nhiều lần OK (set lại cùng config)

Run
---
    cd /Volumes/Space/Claude/Projects/zoom-calendar-bot
    .venv/bin/python3.14 scripts/fix_calendar_reminders.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bot import db  # noqa: E402
from bot.calendar_client import CalendarClient  # noqa: E402
from bot.permissions import CALENDAR_PERSONAL_ID, CALENDAR_TEAM_ID  # noqa: E402

POPUP_ONLY_REMINDERS = {
    "useDefault": False,
    "overrides": [
        {"method": "popup", "minutes": 1440},  # 1 ngày trước
        {"method": "popup", "minutes": 30},    # 30 phút trước
    ],
}


def _calendar_id_for_row(row: db.EventRow) -> str:
    """Cùng logic với handlers._calendar_id_for_row."""
    if (row.chat_mode or "personal") == "group":
        cal = CALENDAR_TEAM_ID()
        if cal:
            return cal
    return CALENDAR_PERSONAL_ID()


def main() -> int:
    rows = db.list_recent(limit=200)
    active = [r for r in rows if r.status == "active" and r.calendar_event_id]
    print(f"📋 {len(active)} lịch active có Calendar ID — bắt đầu patch reminders\n")

    cal = CalendarClient()
    fixed = 0
    skipped = 0
    for r in active:
        target_cal = _calendar_id_for_row(r)
        try:
            # Direct API call vì calendar_client.patch_event không expose reminders
            cal._service.events().patch(  # noqa: SLF001
                calendarId=target_cal,
                eventId=r.calendar_event_id,
                body={"reminders": POPUP_ONLY_REMINDERS},
                sendUpdates="none",  # KHÔNG email khách "lịch đã update"
            ).execute()
            fixed += 1
            print(f"  ✅ id={r.id:3d} | {r.start_local[:16]} | {r.topic[:50]}")
        except Exception as e:
            skipped += 1
            print(f"  ❌ id={r.id:3d} | {r.topic[:50]} → {e}")

    print(f"\nXong: {fixed} fixed, {skipped} skipped/error")
    return 0 if skipped == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
