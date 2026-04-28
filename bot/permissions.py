"""Permission + chat-mode resolution cho Phase 3 multi-user.

Mỗi update từ Telegram → resolve thành 1 `Context` object chứa:
- user_id + display_name (từ users_config nếu có, hoặc raw từ Telegram)
- chat_mode: "personal" / "group" / "reject"
- is_admin
- can_proceed: bool — caller dùng để gate command

Convention: handlers gọi `resolve_context(update)` ở đầu → check `ctx.gate`
field. Nếu không pass, dùng `ctx.reject_message` để reply (đã format sẵn).
Sau đó CALL `audit(ctx, command, params, result, error_message)` để log.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from telegram import Update

from bot import db
from bot.users_config import UserConfig, get_user, is_admin

log = logging.getLogger(__name__)


# Env vars Phase 3 — đọc lazy để không break khi env chưa set lên Render
def _env_int(name: str, default: int = 0) -> int:
    v = os.environ.get(name, "").strip()
    try:
        return int(v)
    except (ValueError, TypeError):
        return default


def OWNER_USER_ID() -> int:
    """User ID của chị Yến — owner duy nhất, dùng cho personal mode + admin gate."""
    return _env_int("TELEGRAM_OWNER_USER_ID", 8173041182)


def GROUP_CHAT_ID() -> int:
    """Chat ID của group "JA Scheduler Team" (negative number)."""
    return _env_int("TELEGRAM_GROUP_CHAT_ID", 0)


def CALENDAR_PERSONAL_ID() -> str:
    return os.environ.get("GOOGLE_CALENDAR_PERSONAL_ID", "primary").strip() or "primary"


def CALENDAR_TEAM_ID() -> str:
    return os.environ.get("GOOGLE_CALENDAR_TEAM_ID", "").strip()


@dataclass
class RequestContext:
    """Resolved identity + permission cho 1 update.

    Caller gate command bằng cách kiểm tra `mode != 'reject'`. Nếu reject,
    `reject_message` chứa text VN ready-to-reply.
    """
    user_id: int
    display_name: str
    user_config: UserConfig | None  # None = unknown user (không trong USERS)
    mode: str                        # "personal" / "group" / "reject"
    is_admin: bool
    chat_id: int
    chat_type: str                   # "private" / "group" / "supergroup" / "channel"
    chat_title: str                  # "" với private chat
    reject_message: str = ""         # set khi mode == "reject"

    # Calendar identity tương ứng
    calendar_id: str = ""            # primary cho personal, team cho group


def resolve_context(update: Update) -> RequestContext:
    """Convert Telegram update → RequestContext theo spec Phase 3.

    Logic:
      1. Lấy chat_id + user_id + identity
      2. Map chat_id → mode:
         - chat_id == OWNER_USER_ID && user_id == OWNER_USER_ID → personal
         - chat_id == GROUP_CHAT_ID → group
         - khác → reject
      3. Group mode + user_id không trong USERS → reject (trừ /whoami caller xử lý)
      4. Personal mode + user_id != OWNER → reject (an toàn double check)
      5. Resolve calendar_id, is_admin từ user_config
    """
    user = update.effective_user
    chat = update.effective_chat
    if user is None or chat is None:
        return _reject_context(0, "", 0, "", "", "Không xác định được identity")

    user_id = user.id
    chat_id = chat.id
    chat_type = chat.type
    chat_title = chat.title or ""

    # Tên hiển thị: ưu tiên users_config (Hải Yến/Hương/Thuỳ), fallback raw Telegram
    user_cfg = get_user(user_id)
    if user_cfg:
        display = user_cfg.display_name
    else:
        first = (user.first_name or "").strip()
        last = (user.last_name or "").strip()
        display = " ".join(p for p in [first, last] if p) or f"user#{user_id}"

    owner = OWNER_USER_ID()
    group_id = GROUP_CHAT_ID()

    # Personal mode
    if chat_id == owner and user_id == owner:
        return RequestContext(
            user_id=user_id, display_name=display, user_config=user_cfg,
            mode="personal", is_admin=True,
            chat_id=chat_id, chat_type=chat_type, chat_title=chat_title,
            calendar_id=CALENDAR_PERSONAL_ID(),
        )

    # Group mode
    if group_id != 0 and chat_id == group_id:
        if user_cfg is None:
            return _reject_context(
                user_id, display, chat_id, chat_type, chat_title,
                "❌ Bạn chưa được cấp quyền dùng bot. "
                "Liên hệ Hải Yến để được thêm vào USERS config.",
            )
        return RequestContext(
            user_id=user_id, display_name=display, user_config=user_cfg,
            mode="group", is_admin=is_admin(user_id),
            chat_id=chat_id, chat_type=chat_type, chat_title=chat_title,
            calendar_id=CALENDAR_TEAM_ID(),
        )

    # Khác → reject (chat lạ)
    return _reject_context(
        user_id, display, chat_id, chat_type, chat_title,
        "❌ Chat này không được phép dùng bot. "
        "Bot chỉ phục vụ chat 1-1 chị Hải Yến hoặc group JA Scheduler Team.",
    )


def _reject_context(
    user_id: int, display: str, chat_id: int,
    chat_type: str, chat_title: str, reason: str,
) -> RequestContext:
    return RequestContext(
        user_id=user_id, display_name=display or "UNAUTHORIZED_USER",
        user_config=None, mode="reject", is_admin=False,
        chat_id=chat_id, chat_type=chat_type, chat_title=chat_title,
        reject_message=reason, calendar_id="",
    )


def can_modify_event(ctx: RequestContext, row: db.EventRow) -> tuple[bool, str]:
    """Check ctx có quyền modify (sửa/xoá/sync) `row` không.

    Trả (allowed, reason). reason chỉ dùng khi allowed=False để reply user.

    Permission matrix:
      - Personal mode: full quyền với mọi row personal của chị
      - Group-Admin: full quyền với mọi row group
      - Group-Member: chỉ row mình tạo
      - Cross-mode: KHÔNG cho phép (personal user thao tác lịch group, etc.)
    """
    if ctx.mode == "reject":
        return False, ctx.reject_message

    # Cross-mode block
    row_mode = row.chat_mode or "personal"
    if ctx.mode != row_mode:
        return False, (
            f"❌ Lịch này thuộc chế độ *{row_mode}*, "
            f"chị/anh đang ở chế độ *{ctx.mode}*. "
            f"Không thao tác cross-mode được."
        )

    # Personal: chỉ owner thao tác
    if ctx.mode == "personal":
        return True, ""

    # Group: admin full, member chỉ own
    if ctx.is_admin:
        return True, ""

    # Group-Member: check ownership
    if row.created_by_user_id == ctx.user_id:
        return True, ""

    creator = row.created_by_display_name or f"user#{row.created_by_user_id}"
    return False, (
        f"❌ Bạn chỉ thao tác được lịch chính mình tạo. "
        f"Lịch id={row.id} do *{creator}* tạo."
    )


# ── Audit helper — wrap db.log_audit với context ──────────────────────────────
def audit(
    ctx: RequestContext,
    command: str,
    *,
    params: dict | str = "",
    result: str = "success",
    error_message: str = "",
) -> None:
    """Wrap log_audit — auto fill user_id/display/mode từ ctx.

    `params` có thể dict (sẽ JSON-stringify) hoặc string.
    """
    if isinstance(params, dict):
        try:
            params_str = json.dumps(params, ensure_ascii=False, default=str)
        except Exception:
            params_str = str(params)
    else:
        params_str = str(params or "")
    db.log_audit(
        user_id=ctx.user_id,
        display_name=ctx.display_name,
        chat_mode=ctx.mode,
        command=command,
        params=params_str,
        result=result,
        error_message=error_message,
    )
