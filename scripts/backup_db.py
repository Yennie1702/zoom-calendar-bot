"""Backup Turso DB → data/backups/db_<TIMESTAMP>.sql.gz

Dump tất cả 3 bảng (events, bot_meta, external_reminders_sent) thành plain
SQL với CREATE TABLE + INSERT statements. Gzip rồi lưu local.

Restore: `gunzip -c db_*.sql.gz | sqlite3 restored.db`
hoặc paste SQL vào `turso db shell <url>`.

Cleanup: xoá file > BACKUP_RETENTION_DAYS.

Run: venv/bin/python scripts/backup_db.py
"""
from __future__ import annotations

import gzip
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot import config, db  # noqa: E402

# ── Config ──────────────────────────────────────────────────────────────────
BACKUP_DIR = Path(__file__).resolve().parent.parent / "data" / "backups"
BACKUP_RETENTION_DAYS = 90
LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

# Tables to dump (order matters: parents first)
TABLES = ["events", "bot_meta", "external_reminders_sent", "members"]


# ── Logging ─────────────────────────────────────────────────────────────────
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_DIR / "backup.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ── Dump logic ──────────────────────────────────────────────────────────────
def _quote_sql(val) -> str:
    """SQLite-style literal escaping."""
    if val is None:
        return "NULL"
    if isinstance(val, (int, float)):
        return str(val)
    s = str(val).replace("'", "''")
    return f"'{s}'"


def _dump_table(conn, table: str) -> str:
    """Return CREATE + INSERT statements for one table."""
    out_lines = [f"-- Table: {table}"]

    # 1. Schema
    schema_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not schema_row or not schema_row[0]:
        log.warning("Table %s không tồn tại hoặc không có schema, skip", table)
        return ""
    out_lines.append(f"DROP TABLE IF EXISTS {table};")
    out_lines.append(f"{schema_row[0]};")

    # 2. Rows
    cur = conn.execute(f"SELECT * FROM {table}")
    rows = cur.fetchall()
    if not rows:
        out_lines.append(f"-- (no rows in {table})")
        out_lines.append("")
        return "\n".join(out_lines) + "\n"

    # Discover columns from first row's keys (sqlite3.Row supports keys())
    try:
        cols = list(rows[0].keys())
    except (AttributeError, TypeError):
        # Turso adapter rows may be tuples — fetch column names via PRAGMA
        info = conn.execute(f"PRAGMA table_info({table})").fetchall()
        cols = [r[1] for r in info]

    col_list = ", ".join(cols)
    for row in rows:
        # Convert row → tuple of values regardless of backend
        if hasattr(row, "keys"):
            vals = [row[c] for c in cols]
        else:
            vals = list(row)
        val_list = ", ".join(_quote_sql(v) for v in vals)
        out_lines.append(
            f"INSERT INTO {table} ({col_list}) VALUES ({val_list});"
        )

    out_lines.append("")
    return "\n".join(out_lines) + "\n"


def dump_database() -> str:
    """Return full SQL dump as a single string."""
    header = (
        f"-- JA Scheduler Bot DB backup\n"
        f"-- Generated: {datetime.now().isoformat()}\n"
        f"-- Source: {'Turso libSQL' if db._using_turso() else 'SQLite local'}\n"
        f"-- Restore: sqlite3 restored.db < this_file.sql\n"
        f"--          OR paste into Turso shell\n\n"
        f"PRAGMA foreign_keys=OFF;\n"
        f"BEGIN TRANSACTION;\n\n"
    )
    parts = [header]
    with db._conn() as conn:
        for table in TABLES:
            try:
                parts.append(_dump_table(conn, table))
            except Exception as e:  # noqa: BLE001
                log.exception("Dump table %s failed: %s", table, e)
                parts.append(f"-- ERROR dumping {table}: {e}\n\n")
    parts.append("COMMIT;\n")
    return "".join(parts)


# ── Save + cleanup ──────────────────────────────────────────────────────────
def save_dump(sql_text: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    out_path = BACKUP_DIR / f"db_{timestamp}.sql.gz"
    with gzip.open(out_path, "wt", encoding="utf-8") as f:
        f.write(sql_text)
    return out_path


def cleanup_old_backups() -> int:
    """Xoá file > BACKUP_RETENTION_DAYS. Trả về số file đã xoá."""
    cutoff = datetime.now() - timedelta(days=BACKUP_RETENTION_DAYS)
    deleted = 0
    if not BACKUP_DIR.exists():
        return 0
    for f in BACKUP_DIR.glob("db_*.sql.gz"):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime)
            if mtime < cutoff:
                f.unlink()
                deleted += 1
                log.info("Cleanup: removed %s (age %dd)", f.name, (datetime.now() - mtime).days)
        except OSError as e:
            log.warning("Cleanup skip %s: %s", f.name, e)
    return deleted


# ── Entry ───────────────────────────────────────────────────────────────────
def main() -> int:
    log.info("=" * 60)
    log.info("DB backup start")
    log.info("Backend: %s", "Turso" if db._using_turso() else "SQLite local")
    try:
        sql = dump_database()
    except Exception as e:  # noqa: BLE001
        log.exception("dump_database failed: %s", e)
        return 1

    out_path = save_dump(sql)
    size_kb = out_path.stat().st_size / 1024
    log.info("✓ Saved %s (%.1f KB)", out_path.name, size_kb)

    deleted = cleanup_old_backups()
    if deleted:
        log.info("Cleanup: deleted %d old backup(s)", deleted)

    # Stats
    total = len(list(BACKUP_DIR.glob("db_*.sql.gz")))
    log.info("Total backups in %s: %d files", BACKUP_DIR, total)
    log.info("DB backup done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
