"""Members directory — sổ thành viên công ty.

**Phase 12 — Backend dual:**

Khi `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` có trong env (production trên
Render) → mọi CRUD đi qua bảng `members` trong Turso, persistent qua restart.
Lần đầu chạy với Turso, nếu bảng rỗng và `data/members.json` có sẵn data
→ auto-seed một lần (gọi `_maybe_seed_from_json()`).

Khi không có Turso (local dev) → fallback đọc/ghi `data/members.json` như cũ.

Public API (dùng bởi `bot.handlers`) không thay đổi:
    list_members() · find_by_email() · add_member() · remove_member()
    emails_in_directory() · page_slice() · member_at_index()

Ngoài ra Phase 13 cần: `find_by_name(query)` — fuzzy lookup theo tên.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from typing import Iterable

from bot import config

log = logging.getLogger(__name__)

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
_MEMBERS_PATH = os.path.join(_DATA_DIR, "members.json")

# Same email regex as parser._RE_EMAIL — keep in sync if loosened
_RE_EMAIL = re.compile(r"^[\w.+\-]+@[\w\-]+\.[\w.\-]+$")

PAGE_SIZE = 8


@dataclass(frozen=True)
class Member:
    name: str
    email: str
    title: str = ""

    def label(self) -> str:
        """One-line label dùng trong picker / `/members` listing."""
        if self.title:
            return f"{self.name} · {self.title} · {self.email}"
        return f"{self.name} · {self.email}"


def _using_turso() -> bool:
    return bool(config.TURSO_DATABASE_URL and config.TURSO_AUTH_TOKEN)


# ── JSON backend (local dev) ──────────────────────────────────────────────────
_json_cache: dict = {"mtime": None, "members": []}


def _read_json_raw() -> dict:
    if not os.path.exists(_MEMBERS_PATH):
        return {"version": 1, "members": []}
    try:
        with open(_MEMBERS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "members" not in data:
            log.warning("members.json cấu trúc lạ — coi như rỗng")
            return {"version": 1, "members": []}
        return data
    except (OSError, json.JSONDecodeError):
        log.exception("Đọc members.json fail — coi như rỗng")
        return {"version": 1, "members": []}


def _atomic_write_json(data: dict) -> None:
    """Tmp + rename để không bao giờ ghi nửa chừng (crash-safe)."""
    os.makedirs(_DATA_DIR, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="members_", suffix=".json", dir=_DATA_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp_path, _MEMBERS_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
    _json_cache["mtime"] = None  # bust cache


def _list_members_json() -> list[Member]:
    """Reload từ disk nếu mtime thay đổi (hot-reload)."""
    try:
        mtime = os.path.getmtime(_MEMBERS_PATH) if os.path.exists(_MEMBERS_PATH) else None
    except OSError:
        mtime = None
    if mtime != _json_cache["mtime"]:
        raw = _read_json_raw()
        members: list[Member] = []
        for m in raw.get("members", []):
            try:
                email = (m.get("email") or "").strip().lower()
                name = (m.get("name") or "").strip()
                title = (m.get("title") or "").strip()
                if not email or not name:
                    continue
                members.append(Member(name=name, email=email, title=title))
            except Exception:
                log.exception("Bỏ qua member bị lỗi: %r", m)
        _json_cache["members"] = members
        _json_cache["mtime"] = mtime
    return list(_json_cache["members"])


def _add_member_json(name: str, email: str, title: str) -> None:
    raw = _read_json_raw()
    raw.setdefault("version", 1)
    raw.setdefault("members", []).append(
        {"name": name, "email": email, "title": title}
    )
    _atomic_write_json(raw)


def _remove_member_json(email: str) -> bool:
    raw = _read_json_raw()
    members = raw.get("members", [])
    new_members = [
        m for m in members
        if (m.get("email") or "").strip().lower() != email.lower()
    ]
    if len(new_members) == len(members):
        return False
    raw["members"] = new_members
    _atomic_write_json(raw)
    return True


# ── Turso backend (production) ────────────────────────────────────────────────
_turso_seeded = False


def _maybe_seed_from_json() -> None:
    """Lần đầu Turso bảng rỗng + JSON có data → seed một lần.

    Chạy idempotent: kiểm tra count trước, không seed lại.
    """
    global _turso_seeded
    if _turso_seeded:
        return
    from bot import db as _db
    try:
        existing = _db.count_members()
    except Exception:
        log.exception("Không kiểm tra được count_members — bỏ qua seed")
        _turso_seeded = True
        return
    if existing > 0:
        _turso_seeded = True
        return
    json_members = _list_members_json()
    if not json_members:
        _turso_seeded = True
        return
    log.info("Seed Turso members từ JSON: %d người", len(json_members))
    for i, m in enumerate(json_members):
        try:
            _db.insert_member(
                email=m.email, name=m.name, title=m.title, sort_order=i,
            )
        except Exception:
            log.exception("Seed thất bại với member %r", m)
    _turso_seeded = True


def _list_members_turso() -> list[Member]:
    _maybe_seed_from_json()
    from bot import db as _db
    try:
        rows = _db.list_members_db()
    except Exception:
        log.exception("Đọc members từ Turso fail — fallback sang JSON")
        return _list_members_json()
    return [
        Member(name=r["name"], email=r["email"], title=r.get("title") or "")
        for r in rows
    ]


def _add_member_turso(name: str, email: str, title: str) -> None:
    from bot import db as _db
    _db.insert_member(email=email, name=name, title=title)


def _remove_member_turso(email: str) -> bool:
    from bot import db as _db
    return _db.delete_member(email)


# ── Public API ────────────────────────────────────────────────────────────────
def list_members() -> list[Member]:
    """Toàn bộ sổ. Backend tuỳ env (Turso production / JSON local)."""
    if _using_turso():
        return _list_members_turso()
    return _list_members_json()


def find_by_email(email: str) -> Member | None:
    if not email:
        return None
    needle = email.strip().lower()
    for m in list_members():
        if m.email == needle:
            return m
    return None


def find_by_name(query: str) -> list[Member]:
    """Phase 13 — fuzzy lookup theo tên (case-insensitive substring).

    Match policy:
      1. Exact name match (case-insensitive) → ưu tiên cao nhất.
      2. Prefix match.
      3. Substring match.

    Trả list theo thứ tự ưu tiên trên. Caller xử lý disambiguation nếu >1 match.
    """
    if not query:
        return []
    q = query.strip().lower()
    if not q:
        return []
    members = list_members()
    exact = [m for m in members if m.name.lower() == q]
    if exact:
        return exact
    prefix = [m for m in members if m.name.lower().startswith(q) and m.name.lower() != q]
    substring = [
        m for m in members
        if q in m.name.lower() and not m.name.lower().startswith(q)
    ]
    return prefix + substring


def add_member(name: str, email: str, title: str = "") -> Member:
    """Append member. Raises ValueError nếu email không hợp lệ / đã tồn tại."""
    name = (name or "").strip()
    email = (email or "").strip().lower()
    title = (title or "").strip()
    if not name:
        raise ValueError("Tên trống.")
    if not _RE_EMAIL.match(email):
        raise ValueError(f"Email không hợp lệ: {email!r}.")
    if find_by_email(email):
        raise ValueError(
            f"Email {email} đã có trong sổ. "
            f"Gõ /members rm {email} trước nếu muốn ghi đè."
        )
    if _using_turso():
        _add_member_turso(name=name, email=email, title=title)
    else:
        _add_member_json(name=name, email=email, title=title)
    return Member(name=name, email=email, title=title)


def remove_member(email: str) -> bool:
    """Xoá member theo email. Trả True nếu có gì để xoá."""
    needle = (email or "").strip().lower()
    if not needle:
        return False
    if _using_turso():
        return _remove_member_turso(needle)
    return _remove_member_json(needle)


def emails_in_directory(emails: Iterable[str]) -> set[str]:
    have = {m.email for m in list_members()}
    return {e.lower() for e in emails if e.lower() in have}


# ── Helpers cho picker UI ─────────────────────────────────────────────────────
def page_slice(page: int) -> tuple[list[Member], int, int]:
    """Trả (members_on_page, total_pages, clamped_page) — page 1-based."""
    members = list_members()
    total = max(1, (len(members) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, total))
    start = (page - 1) * PAGE_SIZE
    return members[start:start + PAGE_SIZE], total, page


def member_at_index(idx: int) -> Member | None:
    members = list_members()
    if 0 <= idx < len(members):
        return members[idx]
    return None


# ── Phase 13 — Resolution helpers ─────────────────────────────────────────────
@dataclass
class ResolutionResult:
    """Output của resolve_token() — một token thô được giải mã thành gì."""
    raw: str                 # token gốc (chưa strip)
    email: str | None        # email cuối cùng (None = không resolve được)
    member: Member | None    # member matched (None nếu là email gõ tay)
    ambiguous: list[Member] = None  # nếu nhiều member cùng match tên
    error: str | None = None         # message tiếng Việt nếu fail

    def __post_init__(self):
        if self.ambiguous is None:
            self.ambiguous = []


def resolve_token(token: str) -> ResolutionResult:
    """Giải 1 token thô (1 phần tử của dòng "Khách:") thành email.

    Quy tắc:
      - Token là email hợp lệ → trả email đó (member=None, không tra sổ).
      - Token là tên (không phải email) → tra sổ:
        * Match đúng 1 → ok, trả email member.
        * Match >1 → ambiguous (caller xử lý).
        * Không match → error.
    """
    raw = token
    s = (token or "").strip()
    if not s:
        return ResolutionResult(raw=raw, email=None, member=None,
                                error="Token trống.")
    # Bỏ dấu @ nếu chị gõ shortcut kiểu @lan
    if s.startswith("@"):
        s = s[1:].strip()
    if _RE_EMAIL.match(s):
        return ResolutionResult(raw=raw, email=s.lower(), member=None)
    # Coi như tên — tra sổ
    matches = find_by_name(s)
    if len(matches) == 1:
        return ResolutionResult(raw=raw, email=matches[0].email, member=matches[0])
    if len(matches) > 1:
        return ResolutionResult(
            raw=raw, email=None, member=None, ambiguous=matches,
            error=f'Tên "{s}" khớp {len(matches)} người trong sổ — em không biết chọn ai.',
        )
    return ResolutionResult(
        raw=raw, email=None, member=None,
        error=f'Em không tìm thấy "{s}" trong sổ và đây không phải email.',
    )


def resolve_attendees_line(line: str) -> tuple[list[str], list[ResolutionResult]]:
    """Phân tích cả dòng "Khách: a, b, c" — split theo dấu phẩy / xuống dòng.

    Trả (resolved_emails_dedupe, problems).
    `problems` chứa các ResolutionResult có error (không match / ambiguous).
    """
    if not line:
        return [], []
    # Split theo dấu phẩy hoặc dấu chấm phẩy
    raw_tokens = re.split(r"[,;\n]+", line)
    emails: list[str] = []
    problems: list[ResolutionResult] = []
    seen: set[str] = set()
    for tok in raw_tokens:
        if not tok.strip():
            continue
        result = resolve_token(tok)
        if result.email:
            if result.email not in seen:
                emails.append(result.email)
                seen.add(result.email)
        else:
            problems.append(result)
    return emails, problems
