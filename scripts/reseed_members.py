"""One-shot: wipe + reseed bảng `members` từ `data/members.json`.

Dùng khi:
  - Lần đầu deploy Phase 12 nhưng Turso đã có data cũ từ test → cần force reseed
  - Bulk import từ JSON (chị edit JSON, chạy script)
  - Clean state để test

Run:
    venv/bin/python scripts/reseed_members.py [--yes]

Mặc định show diff + xác nhận. Pass `--yes` để bỏ confirm.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bot import config, db, directory  # noqa: E402


def load_json_members() -> list[dict]:
    path = PROJECT_ROOT / "data" / "members.json"
    if not path.exists():
        print(f"❌ {path} không tồn tại.")
        sys.exit(1)
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("members") or []


def main() -> int:
    auto_yes = "--yes" in sys.argv or "-y" in sys.argv
    backend = "Turso" if directory._using_turso() else "SQLite local"
    print(f"Backend: {backend}")
    if backend == "SQLite local":
        print(
            "ℹ️  Đang dùng JSON fallback (không có Turso credentials). "
            "Script này không cần thiết — bot đọc trực tiếp data/members.json."
        )
        return 0

    json_members = load_json_members()
    print(f"\n📂 data/members.json có {len(json_members)} thành viên:")
    for i, m in enumerate(json_members, 1):
        print(f"  {i:2}. {m.get('name', '?')}  →  {m.get('email', '?')}")

    current = db.list_members_db()
    print(f"\n🗄  Turso bảng `members` hiện có {len(current)} thành viên:")
    for i, m in enumerate(current, 1):
        print(f"  {i:2}. {m['name']}  →  {m['email']}")

    print(
        f"\n⚠️  Sẽ XOÁ toàn bộ {len(current)} dòng cũ trong Turso "
        f"và INSERT lại {len(json_members)} dòng từ JSON."
    )

    if not auto_yes:
        ans = input("Tiếp tục? (yes/no): ").strip().lower()
        if ans not in ("y", "yes"):
            print("❌ Huỷ.")
            return 1

    # Wipe
    with db._conn() as c:
        c.execute("DELETE FROM members")

    # Reseed
    for i, m in enumerate(json_members):
        email = (m.get("email") or "").strip().lower()
        name = (m.get("name") or "").strip()
        title = (m.get("title") or "").strip()
        if not email or not name:
            print(f"  ⚠️  Skip dòng {i+1}: thiếu email/name")
            continue
        db.insert_member(email=email, name=name, title=title, sort_order=i)

    final = db.list_members_db()
    print(f"\n✅ Done. Turso giờ có {len(final)} thành viên:")
    for i, m in enumerate(final, 1):
        print(f"  {i:2}. {m['name']}  →  {m['email']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
