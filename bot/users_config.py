"""USERS config — danh sách thành viên có quyền dùng bot trong Group mode.

**Phase 3.x — repo public-friendly:**

USERS không hardcode trong file này nữa. Load từ env `USERS_CONFIG_JSON`
(JSON string chứa list user dicts). Production set env trên Render dashboard.

Lý do: repo public cho GitHub Actions cron precision <1 phút, nhưng KHÔNG
muốn expose user_id Telegram + email team. Move sensitive config sang env.

Format env `USERS_CONFIG_JSON`:
    [
      {"user_id": 8173041182, "display_name": "...", "email": "...",
       "role": "admin", "team": "...", "calendar_color": "6",
       "title_prefix": "[John Academy] ", "signature": "...",
       "telegram_username": ""},
      {...}
    ]

Local dev: nếu env trống, USERS = {} → bot reject mọi user trong group mode.
Set env qua `.env` file (gitignored) để test local.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UserConfig:
    user_id: int
    display_name: str
    email: str
    role: str           # "admin" | "member"
    team: str
    calendar_color: str  # Google Calendar eventColorId (1-11)
    title_prefix: str    # prefix Calendar event summary (vd "[John Academy] ")
    signature: str       # multi-line signature embed vào description
    telegram_username: str = ""  # mention reminder (vd "QuynhHuongNgo")


_FIELDS = (
    "user_id", "display_name", "email", "role", "team",
    "calendar_color", "title_prefix", "signature", "telegram_username",
)


def _load_users() -> dict[int, UserConfig]:
    """Parse USERS_CONFIG_JSON env → dict[user_id → UserConfig].

    Defensive: malformed JSON / missing fields → log warning + skip entry.
    Empty / unset env → trả {} (production phải set, dev có thể bypass).
    """
    raw = os.environ.get("USERS_CONFIG_JSON", "").strip()
    if not raw:
        log.warning(
            "USERS_CONFIG_JSON env empty — group mode sẽ reject mọi user. "
            "Set env trên Render hoặc .env local để config."
        )
        return {}
    try:
        items = json.loads(raw)
    except json.JSONDecodeError as e:
        log.exception("USERS_CONFIG_JSON parse fail: %s", e)
        return {}
    if not isinstance(items, list):
        log.error("USERS_CONFIG_JSON phải là JSON array, nhận %s", type(items))
        return {}

    out: dict[int, UserConfig] = {}
    for i, item in enumerate(items):
        try:
            kwargs = {k: item.get(k) for k in _FIELDS if k in item}
            kwargs["user_id"] = int(kwargs["user_id"])
            kwargs.setdefault("title_prefix", "")
            kwargs.setdefault("telegram_username", "")
            cfg = UserConfig(**kwargs)
            out[cfg.user_id] = cfg
        except (KeyError, TypeError, ValueError) as e:
            log.warning("Bỏ qua USERS_CONFIG_JSON[%d]: %s — %r", i, e, item)
            continue
    return out


# Load once at module import — caller dùng helpers bên dưới.
USERS: dict[int, UserConfig] = _load_users()


def get_user(user_id: int) -> UserConfig | None:
    return USERS.get(user_id)


def is_admin(user_id: int) -> bool:
    u = USERS.get(user_id)
    return u is not None and u.role == "admin"


def is_known(user_id: int) -> bool:
    return user_id in USERS


def list_users() -> list[UserConfig]:
    """Trả list theo thứ tự admin trước, sau đó members."""
    return sorted(
        USERS.values(),
        key=lambda u: (0 if u.role == "admin" else 1, u.display_name),
    )
