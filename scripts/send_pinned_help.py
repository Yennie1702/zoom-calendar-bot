"""Gửi + tự pin lại 2 tin nhắn hướng dẫn (Part 1/2 + Part 2/2).

Nội dung lấy thẳng từ `bot.handlers._HELP_TEXT_PART1/PART2` để **luôn nhất quán
với `/help`** — không phải maintain 2 nguồn riêng. Mỗi lần thêm tính năng, chỉ
cần update string trong handlers.py rồi re-run script này.

Flow:
  1. Lấy `pinned_message` hiện tại để báo chị (info-only, không thao tác).
  2. `unpinAllChatMessages` — bỏ pin tin cũ (Telegram bot cần quyền pin; ở
     private chat 1-1 bot luôn có quyền).
  3. `sendMessage` Part 1/2 → lưu message_id.
  4. `sendMessage` Part 2/2 (disable_notification=True để không kêu noti tiếp).
  5. `pinChatMessage` cho cả 2 tin mới.

Run: venv/bin/python scripts/send_pinned_help.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bot import config, handlers  # noqa: E402

# Sync nguồn duy nhất với /help command — tránh drift giữa pinned & /help
HELP_PART1 = handlers._HELP_TEXT_PART1
HELP_PART2 = handlers._HELP_TEXT_PART2

API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"
CHAT_ID = config.TELEGRAM_ALLOWED_CHAT_ID


def _api(method: str, **payload):
    """POST tới Telegram Bot API. Raise nếu non-200 hoặc ok=False."""
    r = requests.post(f"{API}/{method}", json=payload, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"{method} failed: {data}")
    return data["result"]


def _send(text: str, label: str, *, silent: bool = False) -> int:
    """Gửi tin, trả message_id."""
    result = _api(
        "sendMessage",
        chat_id=CHAT_ID,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        disable_notification=silent,
    )
    msg_id = result["message_id"]
    print(f"  [{label}] sent — message_id={msg_id}, len={len(text)}")
    return msg_id


def _pin(msg_id: int, *, silent: bool = False) -> None:
    _api("pinChatMessage", chat_id=CHAT_ID, message_id=msg_id, disable_notification=silent)
    print(f"  pinned message_id={msg_id}{' (silent)' if silent else ''}")


def _unpin_all() -> None:
    """Bỏ pin tất cả tin cũ. Im lặng nếu không có gì để unpin."""
    _api("unpinAllChatMessages", chat_id=CHAT_ID)
    print("  unpinned all old pinned messages")


def main() -> int:
    print(f"Chat ID: {CHAT_ID}")
    print(f"Help part 1: {len(HELP_PART1)} chars")
    print(f"Help part 2: {len(HELP_PART2)} chars")
    if len(HELP_PART1) > 4096 or len(HELP_PART2) > 4096:
        print("❌ Một part > 4096 chars — Telegram sẽ reject. Sửa lại trước.")
        return 1

    print("\n1. Bỏ pin tin cũ…")
    try:
        _unpin_all()
    except Exception as e:
        print(f"  ⚠️ unpin fail (continue anyway): {e}")

    print("\n2. Gửi tin mới…")
    id_part1 = _send(HELP_PART1, "Part 1/2")
    id_part2 = _send(HELP_PART2, "Part 2/2", silent=True)

    print("\n3. Pin lại 2 tin mới…")
    _pin(id_part1)
    _pin(id_part2, silent=True)

    print("\n✅ Done. Chị check chat thấy 2 tin pin mới (đã unpin tin cũ).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
