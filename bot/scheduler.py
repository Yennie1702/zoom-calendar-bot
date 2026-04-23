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

from telegram import BotCommand
from telegram.constants import ParseMode
from telegram.error import TelegramError

from bot import config, db, external_events
from bot.db import EventRow
from bot.external_events import ExternalOccurrence

log = logging.getLogger(__name__)

# 30 min lead with ±2 min window → ticks every 60s, never skips, never doubles
REMINDER_LEAD_MIN = 30
REMINDER_HALF_WINDOW_MIN = 2
DIGEST_HOUR = 7
TICK_SEC = 60


_BOT_COMMANDS = [
    BotCommand("start", "Bắt đầu + xem hướng dẫn"),
    BotCommand("help", "Hướng dẫn đầy đủ"),
    BotCommand("list", "Danh sách lịch (gõ /list tuần này, /list mai…)"),
    BotCommand("today", "Lịch hôm nay"),
    BotCommand("sync", "Đồng bộ sau khi kéo thả trên Calendar"),
]


async def start_background_tasks(app) -> None:
    """post_init hook — register command menu + start reminder + digest loops once."""
    if getattr(app, "_scheduler_started", False):
        return
    app._scheduler_started = True
    try:
        await app.bot.set_my_commands(_BOT_COMMANDS)
        log.info("Registered %d bot commands for autocomplete", len(_BOT_COMMANDS))
    except TelegramError:
        log.exception("set_my_commands failed (non-fatal)")
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

    # Bot-created lịch (DB) — full reminder with Zoom link
    for t in db.upcoming_unreminded(lower, upper):
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

    # External lịch (trên Calendar nhưng không do bot tạo) — reminder rút gọn
    for occ in external_events.fetch_in_datetime_window(lower, upper):
        if db.is_external_reminded(occ.calendar_event_id, occ.occurrence_iso):
            continue
        text = _format_external_reminder(occ)
        try:
            await app.bot.send_message(
                chat_id=config.TELEGRAM_ALLOWED_CHAT_ID,
                text=text,
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=True,
            )
            db.mark_external_reminded(occ.calendar_event_id, occ.occurrence_iso)
            log.info(
                "External reminder sent: cal=%s occ=%s",
                occ.calendar_event_id, occ.occurrence_iso,
            )
        except TelegramError:
            log.exception(
                "External reminder failed (cal=%s occ=%s)",
                occ.calendar_event_id, occ.occurrence_iso,
            )


def _format_external_reminder(occ: ExternalOccurrence) -> str:
    d = occ.start_dt
    end = d + timedelta(minutes=occ.duration_min)
    time_str = f"{d.hour:02d}:{d.minute:02d}–{end.hour:02d}:{end.minute:02d}"
    att_line = (
        "\n".join(f"  • {e}" for e in occ.attendees)
        if occ.attendees else "  (không)"
    )
    link_line = (
        f"\n🗓 [Mở Calendar]({occ.html_link})" if occ.html_link else ""
    )
    return (
        f"⏰ *Nhắc lịch ~30 phút nữa* — {time_str} 📅 _(từ Calendar)_\n\n"
        f"🏷 *{occ.topic}*\n"
        f"🎯 {occ.agenda or '(không có mô tả)'}\n"
        f"⏱ {occ.duration_min} phút\n"
        f"👥 Khách:\n{att_line}"
        f"{link_line}\n\n"
        f"_Lịch này không do bot tạo — sửa/xoá qua Google Calendar._"
    )


def _format_reminder(row: EventRow, occ_iso: str) -> str:
    d = datetime.fromisoformat(occ_iso)
    end = d + timedelta(minutes=row.duration_min)
    time_str = f"{d.hour:02d}:{d.minute:02d}–{end.hour:02d}:{end.minute:02d}"
    attendees = (
        "\n".join(f"  • {e}" for e in row.attendees)
        if row.attendees else "  (không)"
    )
    occ_tag = " *(1 buổi của lịch lặp)*" if row.recurring else ""
    is_personal = row.provider == "meet"
    if is_personal:
        hy_badge = "🔒 *Lịch HY cá nhân* · "
        link_block = (
            f"🔗 [Google Meet]({row.meet_join_url})" if row.meet_join_url
            else "_(Meet link hiện trên Calendar event.)_"
        )
        title_prefix = "🔒 "
    else:
        hy_badge = ""
        link_block = (
            f"🔗 [Zoom]({row.zoom_join_url})\n"
            f"🆔 `{row.zoom_meeting_id}` · 🔑 `{row.zoom_passcode}`"
        )
        title_prefix = ""
    return (
        f"⏰ *Nhắc lịch ~30 phút nữa* — {hy_badge}{time_str}{occ_tag}\n\n"
        f"🏷 *{title_prefix}{row.topic}* (id={row.id})\n"
        f"🎯 {row.agenda or '(không)'}\n"
        f"⏱ {row.duration_min} phút\n"
        f"👥 Khách:\n{attendees}\n\n"
        f"{link_block}"
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
    externals = external_events.fetch_on_date(now.date())
    text = _format_digest(today, events, externals)
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


def _format_digest(
    day_iso: str,
    items: list[dict],
    externals: list[ExternalOccurrence] | None = None,
) -> str:
    externals = externals or []
    y, m, d = day_iso.split("-")
    dt = datetime.fromisoformat(day_iso + "T00:00:00")
    header = f"☀️ *Lịch hôm nay* — {_WEEKDAY_VI[dt.weekday()]} {int(d)}/{int(m)}/{y}"
    total = len(items) + len(externals)
    if total == 0:
        return header + "\n\n📭 Hôm nay không có lịch nào. Chúc chị một ngày làm việc hiệu quả ☕"

    # Merge both sources into a single time-sorted list
    merged: list[tuple[str, str]] = []  # (occ_iso, rendered_line)
    for it in items:
        row: EventRow = it["row"]
        occ_iso = it["occurrence_iso"]
        occ_dt = datetime.fromisoformat(occ_iso)
        end = occ_dt + timedelta(minutes=row.duration_min)
        time_str = f"{occ_dt.hour:02d}:{occ_dt.minute:02d}–{end.hour:02d}:{end.minute:02d}"
        tag = "🔁" if row.recurring else "🎯"
        hy = "🔒 " if row.provider == "meet" else ""
        att_suffix = f" · 👥 {len(row.attendees)}" if row.attendees else ""
        merged.append((
            occ_iso,
            f"{tag} *{time_str}* — {hy}{row.topic} (id={row.id}){att_suffix}",
        ))
    for occ in externals:
        occ_dt = occ.start_dt
        end = occ_dt + timedelta(minutes=occ.duration_min)
        time_str = f"{occ_dt.hour:02d}:{occ_dt.minute:02d}–{end.hour:02d}:{end.minute:02d}"
        att_suffix = f" · 👥 {len(occ.attendees)}" if occ.attendees else ""
        merged.append((
            occ.occurrence_iso,
            f"📅 *{time_str}* — {occ.topic}{att_suffix}",
        ))
    merged.sort(key=lambda x: x[0])

    lines = [header, f"\n📋 *{total} lịch* xếp theo giờ:\n"]
    for i, (_, rendered) in enumerate(merged, 1):
        lines.append(f"{i}. {rendered}")
    legend = []
    if items:
        legend.append("🎯/🔁 lịch bot tạo")
    if externals:
        legend.append("📅 lịch Calendar (không do bot tạo)")
    if legend:
        lines.append("\n_" + " · ".join(legend) + "_")
    lines.append("_Gõ /list để xem chi tiết lịch bot tạo._")
    return "\n".join(lines)
