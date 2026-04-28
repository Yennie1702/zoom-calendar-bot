"""Phase 3 migration — backfill `chat_mode`, `created_by_user_id`,
`created_by_display_name` cho các lịch cũ.

Idempotent: chỉ UPDATE rows có `created_by_user_id IS NULL`.

Schema ALTER + audit_log CREATE đã chạy tự động qua _ensure_schema()
khi bot khởi động lần đầu sau deploy. Script này CHỈ làm backfill data.

Run:
    venv/bin/python scripts/migrate_phase3.py        # interactive confirm
    venv/bin/python scripts/migrate_phase3.py --yes  # skip confirm
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bot import db  # noqa: E402

OWNER_USER_ID = 8173041182
OWNER_NAME = "Hải Yến"


def _scalar(row, key: str) -> int:
    """Lấy số đầu tiên từ row, support cả sqlite3.Row lẫn libsql."""
    if row is None:
        return 0
    try:
        return int(row[key])
    except (KeyError, IndexError, TypeError):
        return int(row[0])


def main() -> int:
    auto_yes = "--yes" in sys.argv or "-y" in sys.argv
    backend = "Turso" if db._using_turso() else "SQLite local"
    print(f"Backend: {backend}")

    with db._conn() as c:
        # _ensure_schema() đã chạy auto trong _conn() — đảm bảo schema mới có
        total = c.execute("SELECT COUNT(*) AS n FROM events").fetchone()
        unfilled = c.execute(
            "SELECT COUNT(*) AS n FROM events WHERE created_by_user_id IS NULL"
        ).fetchone()
        audit_n = c.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()

    total_n = _scalar(total, "n")
    unfilled_n = _scalar(unfilled, "n")
    audit_count = _scalar(audit_n, "n")

    print(f"\n📊 Trạng thái hiện tại:")
    print(f"  Tổng events:                               {total_n}")
    print(f"  Events chưa có created_by_user_id (NULL): {unfilled_n}")
    print(f"  audit_log rows:                            {audit_count}")

    if unfilled_n == 0:
        print("\n✅ Tất cả events đã có attribution. Không cần backfill.")
        return 0

    print(f"\n⚠️  Sẽ UPDATE {unfilled_n} rows:")
    print(f"  created_by_user_id      = {OWNER_USER_ID}")
    print(f"  created_by_display_name = {OWNER_NAME!r}")
    print(f"  chat_mode               = 'personal'")
    print(f"  (Chỉ rows đang có created_by_user_id IS NULL — idempotent.)")

    if not auto_yes:
        ans = input("\nTiếp tục? (yes/no): ").strip().lower()
        if ans not in ("y", "yes"):
            print("❌ Huỷ.")
            return 1

    with db._conn() as c:
        c.execute(
            "UPDATE events SET "
            "created_by_user_id = ?, "
            "created_by_display_name = ?, "
            "chat_mode = ? "
            "WHERE created_by_user_id IS NULL",
            (OWNER_USER_ID, OWNER_NAME, "personal"),
        )
        after = c.execute(
            "SELECT COUNT(*) AS n FROM events WHERE created_by_user_id IS NULL"
        ).fetchone()

    after_n = _scalar(after, "n")
    print(f"\n✅ Backfill xong. Còn {after_n} rows NULL (mong đợi 0).")
    return 0 if after_n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
