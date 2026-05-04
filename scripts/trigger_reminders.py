"""External reminder trigger — chạy mỗi 5 phút trên GitHub Actions.

Check window 25-35 phút trước mỗi event sắp tới (cả lịch DB + Calendar). Nếu
event nào vào window và chưa được mark reminded → gửi tin nhắn nhắc qua
Telegram Bot API.

Phase 3:
- Lịch chat_mode='personal' → gửi vào TELEGRAM_OWNER_USER_ID (chat 1-1 chị Yến)
- Lịch chat_mode='group'    → gửi vào TELEGRAM_GROUP_CHAT_ID (group team) +
  mention người tạo (@username nếu có, fallback tg://user?id= link)
- Lịch external (Calendar không do bot tạo) → vẫn gửi vào personal chat
  của chị Yến (Calendar primary), không vào group (chị Yến tự xử lý)

Idempotent qua `events.reminders_sent` (DB) và `external_reminders_sent` (DB)
— giống logic của internal scheduler.

KHÔNG cần Render bot alive. Workflow chạy mỗi 5 phút sẽ cover toàn bộ window
25-35 phút (= 10 phút) — kể cả nếu 1-2 lần ping bị trễ vẫn sẽ có ít nhất 1
lần ping rơi vào window.

Required env: TELEGRAM_BOT_TOKEN, TELEGRAM_OWNER_USER_ID,
TELEGRAM_GROUP_CHAT_ID (cho group reminders), TURSO_*, GOOGLE_*.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bot import config, db, external_events, scheduler  # noqa: E402
from bot.permissions import GROUP_CHAT_ID, OWNER_USER_ID  # noqa: E402
from bot.users_config import get_user  # noqa: E402


def _send(text: str, chat_id: int | str) -> None:
    api = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(
        api,
        json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        },
        timeout=30,
    )
    r.raise_for_status()


def _resolve_target(row: db.EventRow) -> int | str:
    """Quyết chat_id đích cho row.

    chat_mode='group' → group chat_id (nếu set, fallback owner chat 1-1)
    chat_mode='personal' (or NULL) → owner chat 1-1
    """
    mode = row.chat_mode or "personal"
    if mode == "group":
        gid = GROUP_CHAT_ID()
        if gid:
            return gid
    # Fallback: chat 1-1 chị Yến (cho personal hoặc khi GROUP_CHAT_ID chưa set)
    owner = OWNER_USER_ID()
    if owner:
        return owner
    # Last resort: env legacy ALLOWED_CHAT_ID
    return config.TELEGRAM_ALLOWED_CHAT_ID


def _format_creator_mention(user_id: int | None) -> str:
    """Render mention cho người tạo lịch group.

    - Có username trong UserConfig → @username (Telegram trigger noti mạnh)
    - Không có → [Tên](tg://user?id=X) markdown link (không noti, nhưng
      vẫn click được mở chat 1-1)
    - Không tìm được user → "(không xác định)"
    """
    if not user_id:
        return "(không xác định)"
    cfg = get_user(user_id)
    if cfg is None:
        return f"user#{user_id}"
    if cfg.telegram_username:
        u = cfg.telegram_username.lstrip("@")
        return f"@{u} ({cfg.display_name})"
    # Fallback: markdown link tg://user?id=
    return f"[{cfg.display_name}](tg://user?id={user_id}) ({cfg.team})"


def _format_group_reminder(row: db.EventRow, occ_iso: str) -> str:
    """Format reminder cho lịch group — kèm mention người phụ trách."""
    occ_dt = datetime.fromisoformat(occ_iso)
    end = occ_dt + timedelta(minutes=row.duration_min)
    time_line = (
        f"{occ_dt.hour:02d}:{occ_dt.minute:02d}"
        f"–{end.hour:02d}:{end.minute:02d}, "
        f"{occ_dt.day}/{occ_dt.month}/{occ_dt.year}"
    )
    creator = _format_creator_mention(row.created_by_user_id)

    lines = [
        "⏰ *Còn ~30 phút đến lịch:*",
        f"📋 *{row.topic}*",
        f"👤 Người phụ trách: {creator}",
        f"⏰ {time_line}",
    ]
    if row.zoom_join_url:
        lines.append(f"🔗 [Zoom]({row.zoom_join_url})")
        if row.zoom_meeting_id:
            lines.append(f"🆔 `{row.zoom_meeting_id}` · 🔑 `{row.zoom_passcode}`")
    elif row.meet_join_url:  # HY personal
        lines.append(f"🔗 [Google Meet]({row.meet_join_url})")
    if row.attendees:
        n = len(row.attendees)
        if n <= 5:
            lines.append(f"👥 Khách: {', '.join(row.attendees)}")
        else:
            lines.append(f"👥 Khách: {n} người")
    return "\n".join(lines)


def main() -> int:
    now = scheduler._now_vn()
    # Phương án F (2026-05-02): cron workflow đổi sang */15 → window mở rộng
    # ±10 phút (20-40p trước event, 20 phút wide) để 1 run thường rơi trúng
    # window. Trade-off: đôi khi nhắc 20p hoặc 40p trước thay vì đúng 30p,
    # nhưng đảm bảo KHÔNG miss event (vì cron jitter có thể skip 1 run = 15p).
    LEAD_MIN = scheduler.REMINDER_LEAD_MIN  # 30
    HALF_WIN = max(scheduler.REMINDER_HALF_WINDOW_MIN, 10)  # ≥ 10 phút each side

    lower = now + timedelta(minutes=LEAD_MIN - HALF_WIN)
    upper = now + timedelta(minutes=LEAD_MIN + HALF_WIN)
    print(f"Now: {now.isoformat()} | Window: {lower.time()} → {upper.time()}")

    sent_count = 0

    # 1) Bot-created lịch
    for t in db.upcoming_unreminded(lower, upper):
        row = t["row"]
        occ_iso = t["occurrence_iso"]
        target = _resolve_target(row)
        # Phase 3: chọn format theo chat_mode
        if (row.chat_mode or "personal") == "group":
            text = _format_group_reminder(row, occ_iso)
        else:
            text = scheduler._format_reminder(row, occ_iso)
        try:
            _send(text, target)
            db.mark_reminded(row.id, occ_iso)
            print(f"✅ {row.chat_mode or 'personal'} reminder: id={row.id} "
                  f"occ={occ_iso} → chat_id={target}")
            sent_count += 1
        except Exception as e:
            print(f"❌ Reminder fail (id={row.id}): {e}")

    # 2) External lịch (Calendar không do bot tạo) — luôn gửi vào chat
    # personal chị Yến (lịch external trên Calendar primary của chị, không
    # phải Calendar team). Nếu chị muốn external từ Calendar team cũng
    # nhắc vào group, em làm sau (Phase 3.1).
    try:
        ext_occs = external_events.fetch_in_datetime_window(lower, upper)
    except Exception as e:
        print(f"⚠️  fetch external fail: {e}")
        ext_occs = []

    owner_chat = OWNER_USER_ID() or config.TELEGRAM_ALLOWED_CHAT_ID
    for occ in ext_occs:
        if db.is_external_reminded(occ.calendar_event_id, occ.occurrence_iso):
            continue
        text = scheduler._format_external_reminder(occ)
        try:
            _send(text, owner_chat)
            db.mark_external_reminded(occ.calendar_event_id, occ.occurrence_iso)
            print(f"✅ External reminder: {occ.topic} occ={occ.occurrence_iso}")
            sent_count += 1
        except Exception as e:
            print(f"❌ External reminder fail ({occ.topic}): {e}")

    print(f"Total reminders sent: {sent_count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
