"""USERS config — danh sách thành viên có quyền dùng bot trong Group mode.

Source of truth: file Python này (commit lên git, không lưu DB → review qua PR).
Lý do KHÔNG dùng DB:
- 3 user, hiếm thay đổi → file đơn giản hơn DB CRUD
- Permission là code-level decision (admin vs member) → review qua PR an toàn

Khi thêm user mới:
1. Thêm entry vào USERS dict
2. Push lên GitHub → Render auto deploy
3. User mới gõ /whoami → bot reply identity (hoạt động ngay không cần restart
   thêm vì Render auto restart sau deploy)

User_id lấy từ /whoami của user đó. user_id luôn ổn định, KHÔNG đổi
khi user đổi username/tên Telegram.
"""
from __future__ import annotations

from dataclasses import dataclass


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
    # Phase 3 commit #6 — telegram username để mention thật trong group
    # (vd "@QuynhHuongNgo"). Chỉ trigger notification mạnh khi non-empty.
    # Để empty nếu chưa biết → fallback tg://user?id= link (không noti).
    telegram_username: str = ""


# Phase 3 — initial roster
USERS: dict[int, UserConfig] = {
    8173041182: UserConfig(
        user_id=8173041182,
        display_name="Hải Yến",
        email="nguyenthihaiyen@john.vn",
        role="admin",
        team="John Academy",
        calendar_color="6",  # Tangerine — cam
        title_prefix="[John Academy] ",
        signature=(
            "Trân trọng,\n"
            "Hải Yến | PM dự án | John Academy\n"
            "Zalo/SĐT: 0966863797"
        ),
    ),
    8699500614: UserConfig(
        user_id=8699500614,
        display_name="Quỳnh Hương",
        email="ngoquynhhuong@john.vn",
        role="member",
        team="JoyClub",
        calendar_color="9",  # Blueberry — xanh biển
        title_prefix="",
        signature=(
            "Người phụ trách: Quỳnh Hương - JoyClub\n"
            "Zalo/SĐT: 0352118348"
        ),
    ),
    5069935322: UserConfig(
        user_id=5069935322,
        display_name="Vũ Kim Thuỳ",
        email="vukimthuy@john.vn",
        role="member",
        team="JohnBook",
        calendar_color="10",  # Basil — xanh lá
        title_prefix="",
        signature=(
            "Người phụ trách: Vũ Kim Thuỳ - JohnBook\n"
            "Zalo/SĐT: 0389995944"
        ),
    ),
}


def get_user(user_id: int) -> UserConfig | None:
    return USERS.get(user_id)


def is_admin(user_id: int) -> bool:
    u = USERS.get(user_id)
    return u is not None and u.role == "admin"


def is_known(user_id: int) -> bool:
    return user_id in USERS


def list_users() -> list[UserConfig]:
    """Trả list theo thứ tự admin trước, sau đó members."""
    sorted_users = sorted(
        USERS.values(),
        key=lambda u: (0 if u.role == "admin" else 1, u.display_name),
    )
    return sorted_users
