"""One-shot backfill: update `display_name` cho lịch + audit log đã tồn tại
sau khi đổi tên trong users_config.py.

Mapping (2026-04-29):
  user_id=8699500614  "Hương"  → "Quỳnh Hương"
  user_id=5069935322  "Thuỳ"   → "Vũ Kim Thuỳ"

Idempotent — chạy lại không UPDATE thêm gì (filter WHERE display_name = old).

Run:
    venv/bin/python scripts/migrate_display_names.py        # confirm
    venv/bin/python scripts/migrate_display_names.py --yes  # skip confirm
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bot import db  # noqa: E402

RENAMES = [
    # (user_id, old_name, new_name)
    (8699500614, "Hương", "Quỳnh Hương"),
    (5069935322, "Thuỳ", "Vũ Kim Thuỳ"),
]


def _scalar(row, key: str) -> int:
    if row is None:
        return 0
    try:
        return int(row[key])
    except (KeyError, IndexError, TypeError):
        return int(row[0])


def main() -> int:
    auto_yes = "--yes" in sys.argv or "-y" in sys.argv
    backend = "Turso" if db._using_turso() else "SQLite local"
    print(f"Backend: {backend}\n")

    plan: list[tuple[str, str, int]] = []  # (table, sql, expected_n)
    with db._conn() as c:
        for uid, old, new in RENAMES:
            ev_n = _scalar(
                c.execute(
                    "SELECT COUNT(*) AS n FROM events "
                    "WHERE created_by_user_id = ? AND created_by_display_name = ?",
                    (uid, old),
                ).fetchone(),
                "n",
            )
            au_n = _scalar(
                c.execute(
                    "SELECT COUNT(*) AS n FROM audit_log "
                    "WHERE user_id = ? AND display_name = ?",
                    (uid, old),
                ).fetchone(),
                "n",
            )
            print(f"📊 user_id={uid} ({old} → {new}):")
            print(f"   events.created_by_display_name : {ev_n} rows")
            print(f"   audit_log.display_name         : {au_n} rows")
            if ev_n:
                plan.append(("events", uid, old, new, ev_n))
            if au_n:
                plan.append(("audit_log", uid, old, new, au_n))

    if not plan:
        print("\n✅ Không có row nào cần update — đã backfill rồi.")
        return 0

    print(f"\n⚠️  Sẽ UPDATE tổng {sum(p[4] for p in plan)} rows:")
    for table, uid, old, new, n in plan:
        print(f"   {table}: {n} rows  ({old} → {new}, user_id={uid})")

    if not auto_yes:
        ans = input("\nTiếp tục? (yes/no): ").strip().lower()
        if ans not in ("y", "yes"):
            print("❌ Huỷ.")
            return 1

    with db._conn() as c:
        for uid, old, new in RENAMES:
            c.execute(
                "UPDATE events SET created_by_display_name = ? "
                "WHERE created_by_user_id = ? AND created_by_display_name = ?",
                (new, uid, old),
            )
            c.execute(
                "UPDATE audit_log SET display_name = ? "
                "WHERE user_id = ? AND display_name = ?",
                (new, uid, old),
            )

    # Verify
    print("\n📊 Sau update:")
    with db._conn() as c:
        for uid, old, new in RENAMES:
            old_remain = _scalar(
                c.execute(
                    "SELECT COUNT(*) AS n FROM events "
                    "WHERE created_by_user_id = ? AND created_by_display_name = ?",
                    (uid, old),
                ).fetchone(),
                "n",
            )
            new_have = _scalar(
                c.execute(
                    "SELECT COUNT(*) AS n FROM events "
                    "WHERE created_by_user_id = ? AND created_by_display_name = ?",
                    (uid, new),
                ).fetchone(),
                "n",
            )
            print(f"   user_id={uid}: events có {new_have!r} | còn {old_remain} {old!r} (mong đợi 0)")

    print("\n✅ Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
