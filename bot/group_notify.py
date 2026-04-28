"""Group notification — bot tự gửi message vào TELEGRAM_GROUP_CHAT_ID khi
có sự kiện quan trọng trong Group mode.

3 sự kiện chính (Phase 3 spec):
  1. Lịch mới được tạo
  2. Lịch bị xoá
  3. Lịch được sửa (thời gian / attendees / tiêu đề / Zoom link)

KHÔNG notify cho:
  - Personal mode (chỉ chị thấy)
  - Sửa nội dung mô tả/agenda (Q1.A spec)
  - /list, /mylist, /whoami, /members (lệnh đọc)
  - /sync mà KHÔNG có thay đổi (drift detection trả empty)

Implementation note: bot KHÔNG gọi `send_message` qua PTB Application object
ở mọi place (PTB context không sẵn). Em dùng Telegram Bot API trực tiếp qua
HTTP — đơn giản, không cần truyền Application qua các call chain.

Module này được call từ:
- handlers._do_create (sau khi insert_event success, chat_mode='group')
- handlers._do_edit (sau khi _apply_edit success, chat_mode='group')
- handlers._do_delete (sau khi mark_deleted, chat_mode='group')
- handlers._apply_drift_sync (sau khi sync apply, chat_mode='group')
- handlers._do_delete_occurrence (sau khi cancel instance, chat_mode='group')
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import requests

from bot import config
from bot.db import EventRow
from bot.permissions import GROUP_CHAT_ID

log = logging.getLogger(__name__)


def _send_to_group(text: str) -> None:
    """Gửi text vào group chat. Non-fatal: log lỗi nhưng không raise."""
    chat_id = GROUP_CHAT_ID()
    if not chat_id:
        log.warning("TELEGRAM_GROUP_CHAT_ID chưa set — skip group notify")
        return
    try:
        url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
        r = requests.post(
            url,
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if not r.ok:
            log.warning("Group notify fail: %s %s", r.status_code, r.text[:200])
    except Exception:
        log.exception("Group notify exception (non-fatal)")


def _fmt_time(dt: datetime, duration_min: int) -> str:
    end = dt + timedelta(minutes=duration_min)
    return (
        f"{dt.hour:02d}:{dt.minute:02d}–{end.hour:02d}:{end.minute:02d}, "
        f"{dt.day}/{dt.month}/{dt.year}"
    )


def _fmt_attendees(attendees: list[str]) -> str:
    if not attendees:
        return "(không có khách)"
    if len(attendees) <= 5:
        return ", ".join(attendees)
    return ", ".join(attendees[:5]) + f", +{len(attendees) - 5} người"


# ── 3 events ──────────────────────────────────────────────────────────────────
def notify_create(row: EventRow) -> None:
    """Event 1 — lịch mới tạo trong group mode."""
    if (row.chat_mode or "personal") != "group":
        return
    creator = row.created_by_display_name or "?"
    team = ""
    # Lookup team từ users_config (lazy import tránh circular)
    from bot.users_config import get_user
    cfg = get_user(row.created_by_user_id) if row.created_by_user_id else None
    if cfg:
        team = f" ({cfg.team})"

    text = (
        "📅 *Lịch mới được tạo*\n"
        f"👤 Người tạo: *{creator}*{team}\n"
        f"📋 Tiêu đề: {row.topic}\n"
        f"⏰ Thời gian: {_fmt_time(row.start_dt, row.duration_min)}\n"
        f"👥 Khách mời: {_fmt_attendees(row.attendees)}\n"
        f"🆔 ID: `{row.id}`"
    )
    if row.zoom_join_url:
        text += f"\n🔗 [Zoom]({row.zoom_join_url})"
    _send_to_group(text)


def notify_delete(
    row: EventRow,
    *,
    actor_display_name: str,
    actor_user_id: int | None,
) -> None:
    """Event 2 — lịch bị xoá (hoặc 1 buổi recurring bị xoá)."""
    if (row.chat_mode or "personal") != "group":
        return
    team = ""
    from bot.users_config import get_user
    cfg = get_user(actor_user_id) if actor_user_id else None
    if cfg:
        team = f" ({cfg.team})"
    text = (
        "🗑 *Lịch đã bị xoá*\n"
        f"👤 Xoá bởi: *{actor_display_name}*{team}\n"
        f"📋 Tiêu đề: {row.topic} (đã xoá)\n"
        f"⏰ Thời gian: {_fmt_time(row.start_dt, row.duration_min)} (đã xoá)\n"
        f"🆔 ID: `{row.id}`"
    )
    _send_to_group(text)


def notify_edit(
    row: EventRow,
    *,
    actor_display_name: str,
    actor_user_id: int | None,
    field_label: str,
    old_value: str,
    new_value: str,
) -> None:
    """Event 3 — lịch được sửa: thời gian / attendees / tiêu đề / Zoom link.

    KHÔNG notify cho field "agenda" (nội dung) — Q1.A spec. Caller chịu
    trách nhiệm filter trước khi gọi (vd: handlers._apply_edit chỉ gọi
    notify_edit khi field != 'ag').
    """
    if (row.chat_mode or "personal") != "group":
        return
    team = ""
    from bot.users_config import get_user
    cfg = get_user(actor_user_id) if actor_user_id else None
    if cfg:
        team = f" ({cfg.team})"
    text = (
        "🔄 *Lịch đã được cập nhật*\n"
        f"👤 Sửa bởi: *{actor_display_name}*{team}\n"
        f"📋 Tiêu đề: {row.topic}\n"
        f"✏️ {field_label}:\n"
        f"  • Cũ: {old_value}\n"
        f"  • Mới: {new_value}\n"
        f"🆔 ID: `{row.id}`"
    )
    _send_to_group(text)


def notify_sync(
    row: EventRow,
    *,
    actor_display_name: str,
    actor_user_id: int | None,
    diffs: dict,
) -> None:
    """Event 3 variant — /sync drift apply (Calendar → Bot)."""
    if (row.chat_mode or "personal") != "group":
        return
    if not diffs:
        return  # spec: KHÔNG notify khi /sync không có thay đổi
    # Skip nếu diff chỉ là agenda (nội dung)
    notify_keys = {"start", "duration", "topic", "attendees"}
    if not (set(diffs.keys()) & notify_keys):
        return
    team = ""
    from bot.users_config import get_user
    cfg = get_user(actor_user_id) if actor_user_id else None
    if cfg:
        team = f" ({cfg.team})"

    diff_lines = []
    if "start" in diffs:
        diff_lines.append(f"  • Giờ: {diffs['start'][0]} → {diffs['start'][1]}")
    if "duration" in diffs:
        diff_lines.append(f"  • Thời lượng: {diffs['duration'][0]}p → {diffs['duration'][1]}p")
    if "topic" in diffs:
        diff_lines.append(f"  • Tên: {diffs['topic'][0]} → {diffs['topic'][1]}")
    if "attendees" in diffs:
        old_a = diffs['attendees'][0]
        new_a = diffs['attendees'][1]
        diff_lines.append(
            f"  • Khách: {len(old_a)} → {len(new_a)} người"
        )

    text = (
        "🔄 *Lịch đã được đồng bộ với Calendar*\n"
        f"👤 Sửa bởi: *{actor_display_name}*{team}\n"
        f"📋 Tiêu đề: {row.topic}\n"
        + "\n".join(diff_lines) +
        f"\n🆔 ID: `{row.id}`\n"
        "✅ Zoom + DB đã cập nhật"
    )
    _send_to_group(text)
