"""Fetch + normalize Google Calendar events created OUTSIDE the bot.

Bot-created lịch live in the Turso DB (bot.db). Anything else (lịch chị Yến
tự tạo trên Calendar UI, lịch người khác gửi invite) chỉ tồn tại trên Google
Calendar. This module fetches those external occurrences in a date range and
dedupes against `events.calendar_event_id` so we never double-count a
bot-created lịch.

Consumed by scheduler (digest + 30-min reminder), /today, and /list date-ranged
queries. External events aren't editable via bot — chị phải chỉnh trên Calendar.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Iterable

try:
    from zoneinfo import ZoneInfo
except ImportError:  # Python < 3.9 fallback — shouldn't hit on our stack
    ZoneInfo = None  # type: ignore

from bot import config, db
from bot.calendar_client import CalendarClient

log = logging.getLogger(__name__)

_MAX_AGENDA_CHARS = 400


@dataclass
class ExternalOccurrence:
    """One real meeting on Google Calendar not created by this bot."""

    calendar_event_id: str  # Google Calendar event/instance id
    occurrence_iso: str  # naive local ISO, e.g. "2026-04-27T15:00:00"
    duration_min: int
    topic: str
    agenda: str
    attendees: list[str] = field(default_factory=list)
    html_link: str = ""
    recurring_source_id: str | None = None  # set when this is a recurring instance

    @property
    def start_dt(self) -> datetime:
        return datetime.fromisoformat(self.occurrence_iso)


def _project_tz():
    if ZoneInfo is None:
        return None
    return ZoneInfo(config.TIMEZONE)


def _to_local_naive(dt_str: str) -> datetime | None:
    """Convert Calendar API dateTime (RFC3339) to naive datetime in project TZ."""
    try:
        dt = datetime.fromisoformat(dt_str)
    except ValueError:
        return None
    tz = _project_tz()
    if dt.tzinfo is not None and tz is not None:
        dt = dt.astimezone(tz).replace(tzinfo=None)
    elif dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt


def _rfc3339_range(date_from: date, date_to: date) -> tuple[str, str]:
    """Inclusive date range → RFC3339 window in project TZ.

    Google events().list needs timeMin/timeMax with timezone. We pick the
    project TZ bounds so Asia/Ho_Chi_Minh dates align exactly.
    """
    tz = _project_tz()
    start = datetime.combine(date_from, time.min)
    end = datetime.combine(date_to, time.max)
    if tz is not None:
        start = start.replace(tzinfo=tz)
        end = end.replace(tzinfo=tz)
    return start.isoformat(), end.isoformat()


def _extract_attendees(raw: dict) -> list[str]:
    out: list[str] = []
    for a in raw.get("attendees", []) or []:
        if a.get("self"):
            continue  # skip the organizer's own mailbox
        email = a.get("email")
        if email:
            out.append(email)
    return out


def _summary(raw: dict) -> str:
    s = (raw.get("summary") or "").strip()
    return s or "(Không có tiêu đề)"


def _agenda(raw: dict) -> str:
    desc = (raw.get("description") or "").strip()
    if len(desc) > _MAX_AGENDA_CHARS:
        return desc[:_MAX_AGENDA_CHARS].rstrip() + "…"
    return desc


def _duration_min(start_iso: str, end_iso: str) -> int:
    s = _to_local_naive(start_iso)
    e = _to_local_naive(end_iso)
    if s is None or e is None or e <= s:
        return 30
    return max(1, int((e - s).total_seconds() // 60))


def _normalize_event(raw: dict) -> ExternalOccurrence | None:
    """Convert one Calendar API event → ExternalOccurrence (or None if skippable)."""
    start = raw.get("start") or {}
    end = raw.get("end") or {}
    start_dt = start.get("dateTime")
    end_dt = end.get("dateTime")
    if not start_dt or not end_dt:
        # All-day event — skip (time-based reminder/digest semantics don't fit)
        return None

    occ_local = _to_local_naive(start_dt)
    if occ_local is None:
        return None

    return ExternalOccurrence(
        calendar_event_id=raw.get("id", ""),
        occurrence_iso=occ_local.isoformat(timespec="seconds"),
        duration_min=_duration_min(start_dt, end_dt),
        topic=_summary(raw),
        agenda=_agenda(raw),
        attendees=_extract_attendees(raw),
        html_link=raw.get("htmlLink", ""),
        recurring_source_id=raw.get("recurringEventId"),
    )


def _db_calendar_ids() -> set[str]:
    """All calendar_event_id values currently in the DB (active events).

    Recurring series master ids AND individual cancelled-occurrence tracking
    both live in DB — dedupe is done on the series id since the Calendar API
    expands recurring into instances whose `recurringEventId` points to the
    series id we stored.
    """
    with db._conn() as c:
        rows = c.execute(
            "SELECT calendar_event_id FROM events WHERE status = 'active'"
        ).fetchall()
    ids: set[str] = set()
    for r in rows:
        try:
            v = r["calendar_event_id"]
        except (KeyError, IndexError, TypeError):
            v = r[0] if r else None
        if v:
            ids.add(v)
    return ids


def fetch_in_date_range(
    date_from: date,
    date_to: date,
    *,
    exclude_db: bool = True,
) -> list[ExternalOccurrence]:
    """Return external occurrences whose start falls in [date_from, date_to].

    `exclude_db=True` (default): drop anything whose series id (or instance id
    for non-recurring) matches a bot-created lịch in DB — those are already
    rendered with Zoom links + inline buttons elsewhere.
    """
    try:
        client = CalendarClient()
    except Exception:
        log.exception("CalendarClient init failed — skipping external fetch")
        return []

    time_min, time_max = _rfc3339_range(date_from, date_to)
    try:
        raw_items = client.list_events_in_range(
            time_min_iso=time_min,
            time_max_iso=time_max,
        )
    except Exception:
        log.exception("Calendar list_events_in_range failed")
        return []

    db_ids = _db_calendar_ids() if exclude_db else set()

    out: list[ExternalOccurrence] = []
    for raw in raw_items:
        occ = _normalize_event(raw)
        if occ is None:
            continue
        # Skip cancelled occurrence placeholders if any slipped through
        if raw.get("status") == "cancelled":
            continue
        if exclude_db and (
            occ.calendar_event_id in db_ids
            or (occ.recurring_source_id and occ.recurring_source_id in db_ids)
        ):
            continue
        out.append(occ)

    out.sort(key=lambda x: x.occurrence_iso)
    return out


def fetch_on_date(target_date: date) -> list[ExternalOccurrence]:
    return fetch_in_date_range(target_date, target_date)


def fetch_in_datetime_window(
    window_from: datetime, window_to: datetime
) -> list[ExternalOccurrence]:
    """Narrower filter for the 30-min reminder: only occurrences actually
    starting inside [window_from, window_to] (naive local datetimes)."""
    all_in_day = fetch_in_date_range(
        window_from.date(),
        window_to.date(),
    )
    return [
        occ for occ in all_in_day
        if window_from <= occ.start_dt <= window_to
    ]
