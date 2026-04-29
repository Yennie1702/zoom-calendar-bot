"""Gửi + auto-pin 2 tin hướng dẫn TEAM (Part 1/2 + Part 2/2) vào group
"JA Scheduler Team".

Khác `send_pinned_help.py`:
- Target: TELEGRAM_GROUP_CHAT_ID (group team) thay vì
  TELEGRAM_ALLOWED_CHAT_ID (chat 1-1 chị Yến)
- Content: `_TEAM_HELP_TEXT_PART1/2` từ handlers.py — bỏ admin commands
  (/list, /list_users, /audit, /members add/rm), bỏ HY mode

Flow giống send_pinned_help.py:
  1. unpinAllChatMessages — bỏ pin tin cũ (nếu có)
  2. sendMessage Part 1/2
  3. sendMessage Part 2/2 (silent)
  4. pinChatMessage cả 2

Run: venv/bin/python scripts/send_team_pinned_help.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from bot import config, handlers  # noqa: E402
from bot.permissions import GROUP_CHAT_ID  # noqa: E402

HELP_PART1 = handlers._TEAM_HELP_TEXT_PART1
HELP_PART2 = handlers._TEAM_HELP_TEXT_PART2

API = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}"


def _api(method: str, **payload):
    r = requests.post(f"{API}/{method}", json=payload, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"{method} failed: {data}")
    return data["result"]


def _send(chat_id: int, text: str, label: str, *, silent: bool = False) -> int:
    result = _api(
        "sendMessage",
        chat_id=chat_id,
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=True,
        disable_notification=silent,
    )
    msg_id = result["message_id"]
    print(f"  [{label}] sent — message_id={msg_id}, len={len(text)}")
    return msg_id


def _pin(chat_id: int, msg_id: int, *, silent: bool = False) -> None:
    _api("pinChatMessage", chat_id=chat_id, message_id=msg_id, disable_notification=silent)
    print(f"  pinned message_id={msg_id}{' (silent)' if silent else ''}")


def main() -> int:
    chat_id = GROUP_CHAT_ID()
    if not chat_id:
        print("❌ TELEGRAM_GROUP_CHAT_ID chưa set trong env. Abort.")
        return 1

    print(f"Group chat ID: {chat_id}")
    print(f"Team Help part 1: {len(HELP_PART1)} chars")
    print(f"Team Help part 2: {len(HELP_PART2)} chars")
    if len(HELP_PART1) > 4096 or len(HELP_PART2) > 4096:
        print("❌ Một part > 4096 chars — Telegram sẽ reject. Sửa lại trước.")
        return 1

    print("\n1. Bỏ pin tin team-help cũ (nếu có)…")
    try:
        _api("unpinAllChatMessages", chat_id=chat_id)
        print("  unpinned all old pinned messages")
    except Exception as e:
        print(f"  ⚠️ unpin fail (continue anyway): {e}")

    print("\n2. Gửi tin team-help mới…")
    id_part1 = _send(chat_id, HELP_PART1, "Team Part 1/2")
    id_part2 = _send(chat_id, HELP_PART2, "Team Part 2/2", silent=True)

    print("\n3. Thử pin 2 tin mới (cần bot là admin với quyền pin)…")
    pinned_ok = True
    try:
        _pin(chat_id, id_part1)
        _pin(chat_id, id_part2, silent=True)
    except Exception as e:
        pinned_ok = False
        print(f"  ⚠️ Pin fail: {e}")
        print(f"  → Bot chưa là admin (basic group). Pin TAY 2 tin sau:")
        print(f"     - message_id={id_part1} (Part 1/2)")
        print(f"     - message_id={id_part2} (Part 2/2)")
        print(f"     Long-press tin → Pin trên Telegram client.")

    if pinned_ok:
        print("\n✅ Done. Trong group có 2 tin pin mới.")
    else:
        print("\n⚠️ Tin đã gửi nhưng chưa pin. Pin TAY hoặc promote bot làm admin.")
    return 0 if pinned_ok else 0  # exit 0 anyway — tin đã gửi thành công


if __name__ == "__main__":
    sys.exit(main())
