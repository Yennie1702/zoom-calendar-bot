"""Background scheduler — 30-min pre-meeting reminders + daily 07:00 digest.

Both loops are polling-based (check once per minute) rather than timer-based
to survive Render cold starts, clock skew, and process restarts. Idempotency
is enforced via DB state:

    events.reminders_sent   — JSON list of occurrence ISO starts already sent
    bot_meta.last_digest_date — ISO date of last digest fire

Loops run as asyncio tasks launched from `Application.post_init`, sharing
the same event loop as the webhook listener. No extra thread / process.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta

from telegram.constants import ParseMode
from telegram.error import TelegramError

from bot import config, db
from bot.db import EventRow

log = logging.getLogger(__name__)

# 30 min lead with ±2 min window → ticks every 60s, never skips, never doubles
REMINDER_LEAD_MIN = 30
REMINDER_HALF_WINDOW_MIN = 2
DIGEST_HOUR = 7
TICK_SEC = 60


async def start_background_tasks(app) -> None:
    """post_init hook — start reminder + digest loops once."""
    if getattr(app, "_scheduler_started", False):
        return
    app._scheduler_started = True
    asyncio.create_task(reminder_loop(app))
    asyncio.create_task(daily_digest_loop(app))
    log.info("Scheduler started (reminder + daily digest)")


# ── 30-min reminder ──────────────────────────────────────────────────────────
async def reminder_loop(app) -> None:
    while True:
        try:
            await _reminder_tick(app)
        except Exception:
            log.exception("reminder_tick failed")
        await asyncio.sleep(TICK_SEC)


async def _reminder_tick(app) -> None:
    now = datetime.now()
    lower = now + timedelta(minutes=REMINDER_LEAD_MIN - REMINDER_HALF_WINDOW_MIN)
    upper = now + timedelta(minutes=REMINDER_LEAD_MIN + REMINDER_HALF_WINDOW_MIN)
    targets = db.upcoming_unreminded(lower, upper)
    if not targets:
        return
    for t in targets:
        row: EventRow = t["row"]
        occ_iso: str = t["occurrence_iso"]
        text = _format_reminder(row, occ_iso)
        try:
            await app.bot.send_message(
                chat_id=config.TELEGRAM_ALLOWED_CHAT_ID,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
            db.mark_reminded(row.id, occ_iso)
            log.info("Reminder sent: event=%s occ=%s", row.id, occ_iso)
        except TelegramError:
            log.exception("Reminder send failed (event=%s occ=%s)", row.id, occ_iso)


def _format_reminder(row: EventRow, occ_iso: str) -> str:
    d = datetime.fromisoformat(occ_iso)
    end = d + timedelta(minutes=row.duration_min)
    time_str = f"{d.hour:02d}:{d.minute:02d}–{end.hour:02d}:{end.minute:02d}"
    attendees = (
        "\n".join(f"  • {e}" for e in row.attendees)
        if row.attendees else "  (không)"
    )
    occ_tag = " *(1 buổi của lịch lặp)*" if row.recurring else ""
    return (
        f"⏰ *Nhắc lịch ~30 phút nữa* — {time_str}{occ_tag}\n\n"
        f"🏷 *{row.topic}* (id={row.id})\n"
        f"🎯 {row.agenda or '(không)'}\n"
        f"⏱ {row.duration_min} phút\n"
        f"👥 Khách:\n{attendees}\n\n"
        f"🔗 [Zoom]({row.zoom_join_url})\n"
        f"🆔 `{row.zoom_meeting_id}` · 🔑 `{row.zoom_passcode}`"
    )


# ── Daily digest 07:00 ───────────────────────────────────────────────────────
async def daily_digest_loop(app) -> None:
    while True:
        try:
            await _digest_tick(app)
        except Exception:
            log.exception("digest_tick failed")
        await asyncio.sleep(TICK_SEC)


async def _digest_tick(app) -> None:
    now = datetime.now()
    if now.hour != DIGEST_HOUR:
        return
    today = now.date().isoformat()
    if db.get_meta("last_digest_date") == today:
        return
    events = db.events_on_date(today)
    text = _format_digest(today, events)
    try:
        await app.bot.send_message(
            chat_id=config.TELEGRAM_ALLOWED_CHAT_ID,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
        db.set_meta("last_digest_date", today)
        log.info("Daily digest sent for %s (%d lịch)", today, len(events))
    except TelegramError:
        log.exception("Digest send failed for %s", today)


_WEEKDAY_VI = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]


def _format_digest(day_iso: str, items: list[dict]) -> str:
    y, m, d = day_iso.split("-")
    dt = datetime.fromisoformat(day_iso + "T00:00:00")
    header = f"☀️ *Lịch hôm nay* — {_WEEKDAY_VI[dt.weekday()]} {int(d)}/{int(m)}/{y}"
    if not items:
        return header + "\n\n📭 Hôm nay không có lịch nào. Chúc chị một ngày làm việc hiệu quả ☕"
    lines = [header, f"\n📋 *{len(items)} lịch* xếp theo giờ:\n"]
    for i, it in enumerate(items, 1):
        row: EventRow = it["row"]
        occ_dt = datetime.fromisoformat(it["occurrence_iso"])
        end = occ_dt + timedelta(minutes=row.duration_min)
        time_str = f"{occ_dt.hour:02d}:{occ_dt.minute:02d}–{end.hour:02d}:{end.minute:02d}"
        tag = "🔁" if row.recurring else "·"
        att_count = len(row.attendees)
        att_suffix = f" · 👥 {att_count}" if att_count else ""
        lines.append(
            f"{i}. {tag} *{time_str}* — {row.topic} (id={row.id}){att_suffix}"
        )
    lines.append("\n_Gõ /list để xem chi tiết từng lịch._")
    return "\n".join(lines)
