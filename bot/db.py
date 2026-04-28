"""Event storage — SQLite local (dev) hoặc Turso libSQL remote (production).

Khi `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` có trong env → dùng Turso remote
qua `libsql-client` (pure Python, HTTP/HTTPS). Ngược lại → SQLite file tại
`./data/events.db` (gitignored).

Một lớp adapter nhỏ (`_TursoConn`) expose API giống sqlite3
(`execute().fetchone()`, `commit()`, `close()`, `lastrowid`) để các hàm bên
dưới dùng được chung code với cả hai backend.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterator

from bot import config

log = logging.getLogger(__name__)

_DB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_DB_PATH = os.path.join(_DB_DIR, "events.db")

_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        topic TEXT NOT NULL,
        start_local TEXT NOT NULL,
        duration_min INTEGER NOT NULL,
        agenda TEXT NOT NULL DEFAULT '',
        attendees TEXT NOT NULL,
        recurring TEXT,
        zoom_meeting_id TEXT NOT NULL,
        zoom_join_url TEXT NOT NULL,
        zoom_passcode TEXT NOT NULL DEFAULT '',
        calendar_event_id TEXT NOT NULL,
        calendar_event_link TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'active'
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_events_status_start
        ON events (status, start_local DESC)
    """,
    # Phase 12 — sổ thành viên công ty (sync giữa local JSON và Turso)
    """
    CREATE TABLE IF NOT EXISTS members (
        email TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_members_sort
        ON members (sort_order, email)
    """,
    # Phase 3 — audit log cho multi-user (2026-04-28)
    """
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        user_id INTEGER,
        display_name TEXT,
        chat_mode TEXT,
        command TEXT,
        params TEXT,
        result TEXT,
        error_message TEXT
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_audit_timestamp
        ON audit_log (timestamp DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS ix_audit_user_id
        ON audit_log (user_id)
    """,
]


@dataclass
class EventRow:
    id: int
    topic: str
    start_local: str
    duration_min: int
    agenda: str
    attendees: list[str]
    recurring: dict | None
    zoom_meeting_id: str
    zoom_join_url: str
    zoom_passcode: str
    calendar_event_id: str
    calendar_event_link: str
    status: str
    created_at: str
    updated_at: str
    cancelled_occurrences: list[str] = None
    reminders_sent: list[str] = None
    provider: str = "zoom"  # "zoom" (default) or "meet" (lịch HY cá nhân)
    meet_join_url: str = ""
    # Phase 3 — multi-user attribution
    created_by_user_id: int | None = None
    created_by_display_name: str = ""
    chat_mode: str = "personal"  # "personal" (default) or "group"

    def __post_init__(self):
        if self.cancelled_occurrences is None:
            self.cancelled_occurrences = []
        if self.reminders_sent is None:
            self.reminders_sent = []

    @property
    def start_dt(self) -> datetime:
        return datetime.fromisoformat(self.start_local)


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _using_turso() -> bool:
    return bool(config.TURSO_DATABASE_URL and config.TURSO_AUTH_TOKEN)


# ─── Turso adapter: expose sqlite3-like API over libsql-client ────────────────
class _TursoCursor:
    def __init__(self, rs):
        self._rs = rs
        self.lastrowid = rs.last_insert_rowid

    def fetchone(self):
        return self._rs.rows[0] if self._rs.rows else None

    def fetchall(self):
        return list(self._rs.rows)


class _TursoConn:
    """Pretends to be a sqlite3 Connection for _conn() callers."""

    def __init__(self, client):
        self._client = client
        self._last_cursor: _TursoCursor | None = None

    def execute(self, sql: str, params: tuple | list = ()) -> _TursoCursor:
        rs = self._client.execute(sql, list(params) if params else [])
        cur = _TursoCursor(rs)
        self._last_cursor = cur
        return cur

    def commit(self) -> None:
        # libsql-client auto-commits each execute — no-op here.
        pass

    def close(self) -> None:
        self._client.close()


def _open_turso_client():
    """libsql:// URL → convert to https:// for Hrana-over-HTTP (more firewall-friendly)."""
    from libsql_client import create_client_sync  # lazy import
    url = config.TURSO_DATABASE_URL
    if url.startswith("libsql://"):
        url = "https://" + url[len("libsql://"):]
    return create_client_sync(url=url, auth_token=config.TURSO_AUTH_TOKEN)


_SCHEMA_APPLIED = False


def _ensure_schema(c) -> None:
    """Apply schema + idempotent migrations. Run once per process."""
    global _SCHEMA_APPLIED
    if _SCHEMA_APPLIED:
        return
    for stmt in _SCHEMA_STATEMENTS:
        c.execute(stmt)
    # Migration: cancelled_occurrences added 2026-04-21 for recurring series
    try:
        c.execute(
            "ALTER TABLE events ADD COLUMN cancelled_occurrences TEXT DEFAULT '[]'"
        )
    except Exception:  # noqa: BLE001 — both sqlite3 and libsql raise when col exists
        pass
    # Migration: reminders_sent added 2026-04-23 for 30-min pre-meeting reminders
    try:
        c.execute(
            "ALTER TABLE events ADD COLUMN reminders_sent TEXT DEFAULT '[]'"
        )
    except Exception:  # noqa: BLE001
        pass
    # Migration: provider + meet_join_url added 2026-04-23 for lịch HY cá nhân (Meet)
    try:
        c.execute(
            "ALTER TABLE events ADD COLUMN provider TEXT NOT NULL DEFAULT 'zoom'"
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        c.execute(
            "ALTER TABLE events ADD COLUMN meet_join_url TEXT NOT NULL DEFAULT ''"
        )
    except Exception:  # noqa: BLE001
        pass
    # Phase 3 — multi-user attribution (2026-04-28). NULL cho rows cũ;
    # backfill bằng `scripts/migrate_phase3.py` để gán owner=Hải Yến,
    # chat_mode='personal'. Code mới luôn ghi non-null.
    try:
        c.execute("ALTER TABLE events ADD COLUMN created_by_user_id INTEGER")
    except Exception:  # noqa: BLE001
        pass
    try:
        c.execute("ALTER TABLE events ADD COLUMN created_by_display_name TEXT")
    except Exception:  # noqa: BLE001
        pass
    try:
        c.execute("ALTER TABLE events ADD COLUMN chat_mode TEXT")
    except Exception:  # noqa: BLE001
        pass
    # Meta KV table — used by daily digest + any future singleton state
    c.execute(
        "CREATE TABLE IF NOT EXISTS bot_meta (key TEXT PRIMARY KEY, value TEXT)"
    )
    # Dedup store for 30-min reminders on external (non-bot-created) Calendar
    # events. Composite PK = (calendar_event_id, occurrence_iso) so recurring
    # external series get one row per occurrence.
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS external_reminders_sent (
            calendar_event_id TEXT NOT NULL,
            occurrence_iso TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            PRIMARY KEY (calendar_event_id, occurrence_iso)
        )
        """
    )
    c.commit()
    _SCHEMA_APPLIED = True


@contextmanager
def _conn() -> Iterator[Any]:
    if _using_turso():
        c = _TursoConn(_open_turso_client())
    else:
        os.makedirs(_DB_DIR, exist_ok=True)
        c = sqlite3.connect(_DB_PATH)
        c.row_factory = sqlite3.Row
    try:
        _ensure_schema(c)
        yield c
        c.commit()
    finally:
        c.close()


def _row_to_event(r) -> EventRow:
    # libsql-client rows + sqlite3.Row both support r["col"] lookup
    try:
        cancelled_raw = r["cancelled_occurrences"]
    except (KeyError, IndexError):
        cancelled_raw = "[]"
    try:
        cancelled = json.loads(cancelled_raw or "[]")
    except (TypeError, json.JSONDecodeError):
        cancelled = []
    try:
        reminders_raw = r["reminders_sent"]
    except (KeyError, IndexError):
        reminders_raw = "[]"
    try:
        reminders = json.loads(reminders_raw or "[]")
    except (TypeError, json.JSONDecodeError):
        reminders = []
    try:
        provider = (r["provider"] or "zoom")
    except (KeyError, IndexError):
        provider = "zoom"
    try:
        meet_join_url = r["meet_join_url"] or ""
    except (KeyError, IndexError):
        meet_join_url = ""
    # Phase 3 — attribution fields (NULL cho rows cũ chưa backfill)
    try:
        created_by_user_id = r["created_by_user_id"]
        if created_by_user_id is not None:
            created_by_user_id = int(created_by_user_id)
    except (KeyError, IndexError, TypeError, ValueError):
        created_by_user_id = None
    try:
        created_by_display_name = r["created_by_display_name"] or ""
    except (KeyError, IndexError):
        created_by_display_name = ""
    try:
        chat_mode_v = r["chat_mode"] or "personal"
    except (KeyError, IndexError):
        chat_mode_v = "personal"
    return EventRow(
        id=int(r["id"]),
        topic=r["topic"],
        start_local=r["start_local"],
        duration_min=int(r["duration_min"]),
        agenda=r["agenda"] or "",
        attendees=json.loads(r["attendees"] or "[]"),
        recurring=json.loads(r["recurring"]) if r["recurring"] else None,
        zoom_meeting_id=r["zoom_meeting_id"],
        zoom_join_url=r["zoom_join_url"],
        zoom_passcode=r["zoom_passcode"] or "",
        calendar_event_id=r["calendar_event_id"],
        calendar_event_link=r["calendar_event_link"] or "",
        status=r["status"],
        created_at=r["created_at"],
        updated_at=r["updated_at"],
        cancelled_occurrences=cancelled,
        reminders_sent=reminders,
        provider=provider,
        meet_join_url=meet_join_url,
        created_by_user_id=created_by_user_id,
        created_by_display_name=created_by_display_name,
        chat_mode=chat_mode_v,
    )


def insert_event(
    *,
    topic: str,
    start_local: str,
    duration_min: int,
    agenda: str,
    attendees: list[str],
    recurring: dict | None,
    zoom_meeting_id: str,
    zoom_join_url: str,
    zoom_passcode: str,
    calendar_event_id: str,
    calendar_event_link: str,
    provider: str = "zoom",
    meet_join_url: str = "",
    # Phase 3 — multi-user attribution. Personal flow legacy default = chị Yến.
    created_by_user_id: int = 8173041182,
    created_by_display_name: str = "Hải Yến",
    chat_mode: str = "personal",
) -> int:
    now = _now_iso()
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO events (
                created_at, updated_at, topic, start_local, duration_min, agenda,
                attendees, recurring, zoom_meeting_id, zoom_join_url, zoom_passcode,
                calendar_event_id, calendar_event_link, status,
                provider, meet_join_url,
                created_by_user_id, created_by_display_name, chat_mode
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
            """,
            (
                now, now, topic, start_local, duration_min, agenda,
                json.dumps(attendees, ensure_ascii=False),
                json.dumps(recurring, ensure_ascii=False) if recurring else None,
                zoom_meeting_id, zoom_join_url, zoom_passcode,
                calendar_event_id, calendar_event_link,
                provider, meet_join_url,
                created_by_user_id, created_by_display_name, chat_mode,
            ),
        )
        return int(cur.lastrowid)


def list_recent(
    limit: int = 10, *, active_only: bool = True,
    chat_mode: str | None = None,
    created_by_user_id: int | None = None,
) -> list[EventRow]:
    where = []
    params: list = []
    if active_only:
        where.append("status = 'active'")
    if chat_mode is not None:
        where.append("(chat_mode = ? OR (chat_mode IS NULL AND ? = 'personal'))")
        params.extend([chat_mode, chat_mode])
    if created_by_user_id is not None:
        where.append(
            "(created_by_user_id = ? "
            "OR (created_by_user_id IS NULL AND ? = 8173041182))"
        )
        params.extend([created_by_user_id, created_by_user_id])
    q = "SELECT * FROM events"
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY datetime(start_local) DESC, id DESC LIMIT ?"
    params.append(limit)
    with _conn() as c:
        rows = c.execute(q, tuple(params)).fetchall()
    return [_row_to_event(r) for r in rows]


def search_events(
    *,
    topic_contains: str | None = None,
    attendee_contains: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    active_only: bool = True,
    limit: int = 10,
    offset: int = 0,
    chat_mode: str | None = None,
    created_by_user_id: int | None = None,
) -> list[EventRow]:
    """Filter events by topic keyword / attendee substring / date range.

    - `topic_contains`: case-insensitive LIKE against topic + agenda.
    - `attendee_contains`: substring match inside the JSON attendees column
      (good enough — emails don't contain JSON metacharacters).
    - `date_from` / `date_to`: ISO date strings ('YYYY-MM-DD'), inclusive.
      Compared against `date(start_local)` so full-day range works.
    - Results sorted by start_local DESC (newest first) for consistency with /list.
    """
    where = []
    params: list = []
    if active_only:
        where.append("status = 'active'")
    if topic_contains:
        where.append("(lower(topic) LIKE ? OR lower(agenda) LIKE ?)")
        needle = f"%{topic_contains.lower()}%"
        params.extend([needle, needle])
    if attendee_contains:
        where.append("lower(attendees) LIKE ?")
        params.append(f"%{attendee_contains.lower()}%")
    if date_from:
        where.append("date(start_local) >= ?")
        params.append(date_from)
    if date_to:
        where.append("date(start_local) <= ?")
        params.append(date_to)
    # Phase 3 — chat_mode + ownership filter (NULL fallback cho rows cũ)
    if chat_mode is not None:
        where.append("(chat_mode = ? OR (chat_mode IS NULL AND ? = 'personal'))")
        params.extend([chat_mode, chat_mode])
    if created_by_user_id is not None:
        where.append(
            "(created_by_user_id = ? "
            "OR (created_by_user_id IS NULL AND ? = 8173041182))"
        )
        params.extend([created_by_user_id, created_by_user_id])

    q = "SELECT * FROM events"
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY datetime(start_local) DESC, id DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])
    with _conn() as c:
        rows = c.execute(q, tuple(params)).fetchall()
    return [_row_to_event(r) for r in rows]


def count_events(
    *,
    topic_contains: str | None = None,
    attendee_contains: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    active_only: bool = True,
    chat_mode: str | None = None,
    created_by_user_id: int | None = None,
) -> int:
    """Count rows matching same filter spec as search_events — used for pagination."""
    where = []
    params: list = []
    if active_only:
        where.append("status = 'active'")
    if topic_contains:
        where.append("(lower(topic) LIKE ? OR lower(agenda) LIKE ?)")
        needle = f"%{topic_contains.lower()}%"
        params.extend([needle, needle])
    if attendee_contains:
        where.append("lower(attendees) LIKE ?")
        params.append(f"%{attendee_contains.lower()}%")
    if date_from:
        where.append("date(start_local) >= ?")
        params.append(date_from)
    if date_to:
        where.append("date(start_local) <= ?")
        params.append(date_to)
    if chat_mode is not None:
        where.append("(chat_mode = ? OR (chat_mode IS NULL AND ? = 'personal'))")
        params.extend([chat_mode, chat_mode])
    if created_by_user_id is not None:
        where.append(
            "(created_by_user_id = ? "
            "OR (created_by_user_id IS NULL AND ? = 8173041182))"
        )
        params.extend([created_by_user_id, created_by_user_id])

    q = "SELECT COUNT(*) AS n FROM events"
    if where:
        q += " WHERE " + " AND ".join(where)
    with _conn() as c:
        r = c.execute(q, tuple(params)).fetchone()
    if r is None:
        return 0
    return int(r["n"] if hasattr(r, "keys") or hasattr(r, "__getitem__") else r[0])


def get_event(event_id: int) -> EventRow | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
    return _row_to_event(r) if r else None


def update_event_fields(event_id: int, **fields) -> None:
    if not fields:
        return
    if "attendees" in fields and not isinstance(fields["attendees"], str):
        fields["attendees"] = json.dumps(fields["attendees"], ensure_ascii=False)
    if "recurring" in fields:
        r = fields["recurring"]
        fields["recurring"] = json.dumps(r, ensure_ascii=False) if r else None
    if "cancelled_occurrences" in fields and not isinstance(
        fields["cancelled_occurrences"], str
    ):
        fields["cancelled_occurrences"] = json.dumps(
            fields["cancelled_occurrences"], ensure_ascii=False
        )
    if "reminders_sent" in fields and not isinstance(fields["reminders_sent"], str):
        fields["reminders_sent"] = json.dumps(
            fields["reminders_sent"], ensure_ascii=False
        )
    fields["updated_at"] = _now_iso()
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _conn() as c:
        c.execute(f"UPDATE events SET {cols} WHERE id = ?",
                  (*fields.values(), event_id))


def add_cancelled_occurrence(event_id: int, start_iso: str) -> None:
    row = get_event(event_id)
    if not row:
        return
    if start_iso in row.cancelled_occurrences:
        return
    cancelled = [*row.cancelled_occurrences, start_iso]
    update_event_fields(event_id, cancelled_occurrences=cancelled)


def mark_deleted(event_id: int) -> None:
    update_event_fields(event_id, status="deleted")


def latest_created() -> EventRow | None:
    with _conn() as c:
        r = c.execute(
            "SELECT * FROM events WHERE status = 'active' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return _row_to_event(r) if r else None


def _expand_event_starts(row: EventRow) -> list[datetime]:
    """All concrete occurrence start datetimes for `row`, skipping cancelled."""
    base = row.start_dt
    if not row.recurring:
        return [base]
    count = int(row.recurring.get("count", 1))
    from datetime import timedelta
    starts = [base + timedelta(weeks=i) for i in range(count)]
    cancelled = set(row.cancelled_occurrences or [])
    return [s for s in starts if s.isoformat(timespec="seconds") not in cancelled]


# ── Scheduler-facing queries ──────────────────────────────────────────────────
def upcoming_unreminded(
    window_from: datetime, window_to: datetime
) -> list[dict]:
    """Occurrences starting in [window_from, window_to] that haven't been reminded.

    Returns list of {row: EventRow, occurrence_iso: str}.
    Recurring series are expanded; cancelled occurrences skipped.
    """
    with _conn() as c:
        raw = c.execute("SELECT * FROM events WHERE status = 'active'").fetchall()

    hits: list[dict] = []
    for r in raw:
        ev = _row_to_event(r)
        reminded = set(ev.reminders_sent or [])
        for occ_start in _expand_event_starts(ev):
            if not (window_from <= occ_start <= window_to):
                continue
            occ_iso = occ_start.isoformat(timespec="seconds")
            if occ_iso in reminded:
                continue
            hits.append({"row": ev, "occurrence_iso": occ_iso})
    return hits


def mark_reminded(event_id: int, occurrence_iso: str) -> None:
    row = get_event(event_id)
    if not row:
        return
    if occurrence_iso in (row.reminders_sent or []):
        return
    new_list = [*(row.reminders_sent or []), occurrence_iso]
    update_event_fields(event_id, reminders_sent=new_list)


def events_on_date(iso_date: str) -> list[dict]:
    """All occurrences (active only) whose start date == iso_date.

    Returns list of {row: EventRow, occurrence_iso: str}, sorted by start time.
    """
    with _conn() as c:
        raw = c.execute("SELECT * FROM events WHERE status = 'active'").fetchall()

    hits: list[dict] = []
    for r in raw:
        ev = _row_to_event(r)
        for occ_start in _expand_event_starts(ev):
            if occ_start.date().isoformat() == iso_date:
                hits.append({
                    "row": ev,
                    "occurrence_iso": occ_start.isoformat(timespec="seconds"),
                })
    hits.sort(key=lambda x: x["occurrence_iso"])
    return hits


def get_meta(key: str) -> str | None:
    with _conn() as c:
        r = c.execute("SELECT value FROM bot_meta WHERE key = ?", (key,)).fetchone()
    if r is None:
        return None
    try:
        return r["value"]
    except (KeyError, IndexError, TypeError):
        return r[0] if r else None


def set_meta(key: str, value: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO bot_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def is_external_reminded(calendar_event_id: str, occurrence_iso: str) -> bool:
    with _conn() as c:
        r = c.execute(
            "SELECT 1 FROM external_reminders_sent "
            "WHERE calendar_event_id = ? AND occurrence_iso = ?",
            (calendar_event_id, occurrence_iso),
        ).fetchone()
    return r is not None


def mark_external_reminded(calendar_event_id: str, occurrence_iso: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO external_reminders_sent "
            "(calendar_event_id, occurrence_iso, sent_at) VALUES (?, ?, ?)",
            (calendar_event_id, occurrence_iso, _now_iso()),
        )


# ── Members directory (Phase 12) ──────────────────────────────────────────────
def list_members_db() -> list[dict]:
    """Return members ordered by sort_order then email. List of dicts (raw rows)."""
    with _conn() as c:
        rows = c.execute(
            "SELECT email, name, title, sort_order FROM members "
            "ORDER BY sort_order ASC, email ASC"
        ).fetchall()
    out: list[dict] = []
    for r in rows:
        try:
            out.append({
                "email": r["email"],
                "name": r["name"],
                "title": r["title"] or "",
                "sort_order": int(r["sort_order"]) if r["sort_order"] is not None else 0,
            })
        except (KeyError, IndexError, TypeError):
            out.append({
                "email": r[0], "name": r[1], "title": r[2] or "",
                "sort_order": int(r[3]) if r[3] is not None else 0,
            })
    return out


def insert_member(email: str, name: str, title: str = "", sort_order: int | None = None) -> None:
    """Insert hoặc replace member. Email là PK (case-insensitive — caller normalize trước)."""
    now = _now_iso()
    if sort_order is None:
        # Append cuối list — lấy max sort_order + 1
        with _conn() as c:
            r = c.execute("SELECT COALESCE(MAX(sort_order), -1) AS m FROM members").fetchone()
        try:
            current_max = int(r["m"] if r and (hasattr(r, "keys") or hasattr(r, "__getitem__")) else (r[0] if r else -1))
        except (KeyError, IndexError, TypeError):
            current_max = -1
        sort_order = current_max + 1
    with _conn() as c:
        c.execute(
            "INSERT INTO members (email, name, title, sort_order, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(email) DO UPDATE SET "
            "name = excluded.name, title = excluded.title, updated_at = excluded.updated_at",
            (email, name, title, sort_order, now, now),
        )


def delete_member(email: str) -> bool:
    """Xoá member. Trả True nếu có dòng bị xoá."""
    with _conn() as c:
        # Check tồn tại trước (libsql-client không expose rowcount nhất quán)
        r = c.execute("SELECT 1 FROM members WHERE email = ?", (email,)).fetchone()
        if r is None:
            return False
        c.execute("DELETE FROM members WHERE email = ?", (email,))
    return True


def count_members() -> int:
    with _conn() as c:
        r = c.execute("SELECT COUNT(*) AS n FROM members").fetchone()
    if r is None:
        return 0
    try:
        return int(r["n"])
    except (KeyError, IndexError, TypeError):
        return int(r[0])


def find_conflicts(
    start_local: str,
    duration_min: int,
    *,
    exclude_id: int | None = None,
) -> list[tuple[EventRow, str]]:
    """Return [(event, occurrence_iso)] for every active event whose occurrence
    overlaps the candidate [start_local, start_local + duration_min].

    Recurring events are expanded client-side — cancelled occurrences skipped.
    First overlapping occurrence per event is returned (enough for a warning).
    """
    from datetime import timedelta
    start = datetime.fromisoformat(start_local)
    end = start + timedelta(minutes=duration_min)

    with _conn() as c:
        raw = c.execute("SELECT * FROM events WHERE status = 'active'").fetchall()

    hits: list[tuple[EventRow, str]] = []
    for r in raw:
        ev = _row_to_event(r)
        if exclude_id is not None and ev.id == exclude_id:
            continue
        for occ_start in _expand_event_starts(ev):
            occ_end = occ_start + timedelta(minutes=ev.duration_min)
            if occ_start < end and occ_end > start:
                hits.append((ev, occ_start.isoformat(timespec="seconds")))
                break
    return hits


# ── Phase 3 — Audit log ───────────────────────────────────────────────────────
_AUDIT_PARAMS_MAX = 500


def log_audit(
    *,
    user_id: int | None,
    display_name: str,
    chat_mode: str,
    command: str,
    params: str = "",
    result: str = "success",
    error_message: str = "",
) -> None:
    """Insert 1 dòng audit. `params` truncate để tránh DB blow up.

    `result` enum: "success" / "fail" / "reject" (UNAUTHORIZED + permission deny).
    """
    if params and len(params) > _AUDIT_PARAMS_MAX:
        params = params[:_AUDIT_PARAMS_MAX] + "…(truncated)"
    if error_message and len(error_message) > _AUDIT_PARAMS_MAX:
        error_message = error_message[:_AUDIT_PARAMS_MAX] + "…(truncated)"
    try:
        with _conn() as c:
            c.execute(
                "INSERT INTO audit_log (timestamp, user_id, display_name, "
                "chat_mode, command, params, result, error_message) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _now_iso(), user_id, display_name, chat_mode,
                    command, params, result, error_message,
                ),
            )
    except Exception:
        log.exception("audit_log insert fail (non-fatal)")


def query_audit(
    *,
    limit: int = 20,
    user_id: int | None = None,
    date_from: str | None = None,   # ISO date YYYY-MM-DD
    date_to: str | None = None,
    only_errors: bool = False,
) -> list[dict]:
    """Cho /audit — trả list dict ngắn gọn để format reply."""
    where = []
    params: list = []
    if user_id is not None:
        where.append("user_id = ?")
        params.append(user_id)
    if date_from:
        where.append("date(timestamp) >= ?")
        params.append(date_from)
    if date_to:
        where.append("date(timestamp) <= ?")
        params.append(date_to)
    if only_errors:
        where.append("result IN ('fail', 'reject')")
    q = "SELECT timestamp, user_id, display_name, chat_mode, command, params, result, error_message FROM audit_log"
    if where:
        q += " WHERE " + " AND ".join(where)
    q += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)
    with _conn() as c:
        rows = c.execute(q, tuple(params)).fetchall()
    out: list[dict] = []
    cols = [
        "timestamp", "user_id", "display_name", "chat_mode",
        "command", "params", "result", "error_message",
    ]
    for r in rows:
        d = {}
        for col in cols:
            try:
                d[col] = r[col]
            except (KeyError, IndexError):
                d[col] = None
        out.append(d)
    return out
