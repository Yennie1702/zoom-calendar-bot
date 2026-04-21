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

    def __post_init__(self):
        if self.cancelled_occurrences is None:
            self.cancelled_occurrences = []

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
) -> int:
    now = _now_iso()
    with _conn() as c:
        cur = c.execute(
            """
            INSERT INTO events (
                created_at, updated_at, topic, start_local, duration_min, agenda,
                attendees, recurring, zoom_meeting_id, zoom_join_url, zoom_passcode,
                calendar_event_id, calendar_event_link, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active')
            """,
            (
                now, now, topic, start_local, duration_min, agenda,
                json.dumps(attendees, ensure_ascii=False),
                json.dumps(recurring, ensure_ascii=False) if recurring else None,
                zoom_meeting_id, zoom_join_url, zoom_passcode,
                calendar_event_id, calendar_event_link,
            ),
        )
        return int(cur.lastrowid)


def list_recent(limit: int = 10, *, active_only: bool = True) -> list[EventRow]:
    q = "SELECT * FROM events"
    if active_only:
        q += " WHERE status = 'active'"
    q += " ORDER BY datetime(start_local) DESC, id DESC LIMIT ?"
    with _conn() as c:
        rows = c.execute(q, (limit,)).fetchall()
    return [_row_to_event(r) for r in rows]


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
