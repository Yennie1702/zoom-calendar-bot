"""Telegram command + callback handlers for JA Scheduler Bot.

Two flows live in this module:

CREATE FLOW (Phase 2 baseline)
    text "Tạo lịch …" → parse → preview → [✅|❌] → Zoom+Calendar+DB

MANAGE FLOW (Option B — added 2026-04-21)
    /list → numbered buttons → detail → [✏️|🗑] → per-field edit / confirm delete

State is kept in ctx.chat_data:
    pending       — ParsedCommand awaiting create-confirm
    edit_mode     — {"event_id", "field"} when bot is waiting for a new value
    pending_edit  — {"event_id", "field", "new_value", "display"} awaiting edit-confirm
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from bot import config, db, directory, external_events, formatter, group_notify
from bot.calendar_client import CalendarClient
# Phase 3 — multi-user permission gate
from bot.permissions import (
    RequestContext,
    audit,
    can_modify_event,
    resolve_context,
)
from bot.users_config import UserConfig, get_user, list_users as list_user_configs
from bot.parser import (
    CloneOverrides,
    CloneSpec,
    ParseError,
    ParsedCommand,
    TargetSpec,
    is_personal_prefix,
    parse_clone,
    parse_command,
    parse_edit_duration,
    parse_edit_emails,
    parse_edit_plain,
    parse_edit_time,
    parse_list_args,
    parse_quick_edit,
)
from bot.zoom_client import ZoomClient, build_weekly_recurrence

log = logging.getLogger(__name__)

# Singleton API clients (cheap to hold, auth is lazy)
_zoom = ZoomClient()
_calendar: CalendarClient | None = None  # lazy so startup succeeds w/o Google yet


def _get_calendar() -> CalendarClient:
    global _calendar
    if _calendar is None:
        _calendar = CalendarClient()
    return _calendar


# ── Authorization gate ─────────────────────────────────────────────────────────
def _log_incoming(update: Update) -> None:
    """Dump identity of every incoming update so we can diagnose chat_id mismatches."""
    chat = update.effective_chat
    user = update.effective_user
    msg = update.effective_message
    log.info(
        "INCOMING chat_id=%s type=%s title=%r user_id=%s username=%s text=%r",
        chat.id if chat else None,
        chat.type if chat else None,
        chat.title if chat else None,
        user.id if user else None,
        user.username if user else None,
        (msg.text[:80] if msg and msg.text else None),
    )


def _is_allowed(update: Update) -> bool:
    """Phase 1-2 gate — kept for backward compat. Single chat_id check.

    Phase 3 dùng _gate() resolve theo chat_mode (personal/group). _is_allowed
    không nhận biết group → DEPRECATED nhưng giữ tránh break code chưa migrate.
    """
    _log_incoming(update)
    chat = update.effective_chat
    return chat is not None and chat.id == config.TELEGRAM_ALLOWED_CHAT_ID


async def _reject(update: Update) -> None:
    """Silently drop unauthorized updates — do NOT reply into those chats.

    Replying was noisy (bot spammed '❌ Bot này chỉ phục vụ...' into any chat
    where it was added). Rejection now is log-only; debug via Render logs.
    """
    chat = update.effective_chat
    log.warning(
        "REJECTED chat_id=%s type=%s title=%r user=%s — expected ALLOWED_CHAT_ID=%s",
        chat.id if chat else None,
        chat.type if chat else None,
        chat.title if chat else None,
        update.effective_user.username if update.effective_user else None,
        config.TELEGRAM_ALLOWED_CHAT_ID,
    )


def _list_cmd_for(ctx_chat_data: dict | None = None,
                  mode: str | None = None) -> str:
    """Return '/mylist' nếu group mode, '/list' nếu personal — Phase 3 hint
    đúng cho member trong group (member không dùng được /list)."""
    if mode is None and ctx_chat_data is not None:
        mode = ctx_chat_data.get("request_mode")
    return "/mylist" if mode == "group" else "/list"


def _is_bot_addressed(update: Update, ctx) -> bool:
    """Phase 3.x (2026-05-06): true khi tin nhắn group nhắm trực tiếp tới bot.

    Detect 1 trong 3 case:
    1. Reply vào message của bot (reply_to_message.from_user == bot)
    2. Mention @BotUsername trong text (entity type='mention')
    3. Text mention bot (entity type='text_mention' với user.id = bot)

    Dùng trong group để filter chat phiếm vs lệnh dành cho bot.
    """
    msg = update.message
    if msg is None:
        return False
    bot_username = getattr(ctx.bot, "username", "") or ""
    bot_id = getattr(ctx.bot, "id", None)

    # 1) Reply to bot's own message
    if msg.reply_to_message and msg.reply_to_message.from_user:
        if msg.reply_to_message.from_user.id == bot_id:
            return True

    # 2) Bot mentioned in entities
    entities = msg.entities or []
    text = msg.text or ""
    for ent in entities:
        if ent.type == "mention" and bot_username:
            mentioned = text[ent.offset:ent.offset + ent.length]
            if mentioned.lstrip("@").lower() == bot_username.lower():
                return True
        elif ent.type == "text_mention" and ent.user is not None:
            if ent.user.id == bot_id:
                return True
    return False


# Pending state keys — dùng để check "có đang chờ chị làm gì không" trong group
_PENDING_STATE_KEYS = (
    "pending", "pending_edit", "pending_delete", "pending_sync",
    "edit_mode", "occurrences",
    "pending_quick_disambig", "pending_clone_disambig",
    "ext_edit_mode", "pending_ext_edit", "dir_mode",
)


def _has_pending_state(chat_data) -> bool:
    return any(k in chat_data for k in _PENDING_STATE_KEYS)


_GROUP_MENTION_HELP = (
    "👋 Em là *JA Scheduler* — bot đặt lịch họp Zoom + Google Calendar.\n\n"
    "Trong group này, em chỉ reply khi:\n"
    "• Có người tạo lịch mới (gõ `Tạo lịch ...`)\n"
    "• Hoặc tag em (@JA_Scheduler_bot) — sẽ thấy tin này\n\n"
    "*Cách tạo lịch:*\n"
    "```\n"
    "Tạo lịch [chủ đề]\n"
    "- Lúc: [thời gian, vd 14:00 thứ 4 tuần sau]\n"
    "- Thời lượng: [vd 60p]\n"
    "- Khách: [email/tên cách nhau dấu phẩy]\n"
    "```\n\n"
    "Lệnh khác: /mylist (lịch của em), /sync (đồng bộ Calendar), /help (đầy đủ)."
)


async def _gate(
    update: Update, command: str, *, silent_reject: bool = False,
) -> RequestContext | None:
    """Phase 3 gate — resolve chat_mode + permission, audit log.

    Trả None nếu reject (đã reply user + log audit). Trả ctx nếu pass.

    `silent_reject=True` → KHÔNG reply (cho lệnh public như /whoami muốn
    bypass reject text).
    """
    _log_incoming(update)
    ctx = resolve_context(update)
    if ctx.mode == "reject":
        if not silent_reject and update.effective_message is not None:
            try:
                await update.effective_message.reply_text(ctx.reject_message)
            except Exception:
                log.exception("Failed to send reject message")
        audit(ctx, command, result="reject", error_message=ctx.reject_message)
        return None
    return ctx


# ── /start, /help ──────────────────────────────────────────────────────────────
def _pre(s: str) -> str:
    return f"<pre>{_escape(s)}</pre>"


def _code(s: str) -> str:
    return f"<code>{_escape(s)}</code>"


def _escape(s: str) -> str:
    return (
        s.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


_HELP_TEXT_PART1 = (
    "📖 <b>JA Scheduler Bot — Hướng dẫn (1/2)</b>\n"
    "<i>Bot ghép Zoom + Google Calendar, chạy 24/7 trên Render.</i>\n\n"

    # ─── 1. Quick-reference commands
    "📌 <b>1. LỆNH NHANH</b>\n"
    "• " + _code("/start") + ", " + _code("/help") + " — hướng dẫn\n"
    "• " + _code("/list") + " — quản lý lịch (xem · sửa · xoá · tìm · lọc)\n"
    "• " + _code("/today") + " — lịch hôm nay (digest on-demand)\n"
    "• " + _code("/sync [id]") + " — đồng bộ sau khi kéo thả trên Calendar\n"
    "• " + _code("/members") + " — sổ thành viên công ty (chọn nhanh khi tạo lịch)\n\n"

    # ─── 2. Create
    "🆕 <b>2. TẠO LỊCH</b>\n\n"

    "<b>2A. Lịch công việc (Zoom + Calendar, mời khách)</b>\n"
    "<i>One-time:</i>\n"
    + _pre(
        'Tạo lịch "Tư vấn OKRs - Chị Lan":\n'
        "- Thời gian: 22/4/2026 14:00\n"
        "- Thời lượng: 30 phút\n"
        "- Nội dung: Tư vấn gói Coaching OKRs\n"
        "- Khách: lan@abc.com"
    ) + "\n"
    "<i>Recurring (hàng tuần):</i>\n"
    + _pre(
        'Tạo lịch "Mentor MBOs 42":\n'
        "- Thời gian: 8h30 sáng thứ 4 hàng tuần trong 12 tuần liên tiếp bắt đầu từ 20/5/2026\n"
        "- Thời lượng: 120 phút\n"
        "- Nội dung: Chương trình Mentor MBOs\n"
        "- Mời khách: a@x.vn, b@y.vn"
    ) + "\n"
    "→ Bot parse → preview → bấm <b>✅ Xác nhận tạo</b>.\n"
    "💡 <b>Mẹo dòng Khách:</b>\n"
    "• Gõ tên trong sổ thay email: <code>Khách: Toàn, Hương, Oanh</code>\n"
    "• Mời cả team: <code>Khách: /all</code> (hoặc <code>tất cả</code>)\n"
    "• Mix tự do: <code>Khách: /all, abc@external.com</code>\n"
    "• Trong preview: bấm <b>📋 Sửa danh sách</b> để bỏ bớt người, "
    "<b>📇 Thêm từ sổ</b> để chọn thêm.\n\n"

    "<b>2B. Lịch HY — cá nhân</b> 🔒 <i>(chỉ mình chị, Meet thay Zoom, private)</i>\n"
    "Keyword " + _code("HY") + " thay " + _code("Tạo lịch") + ". Bot:\n"
    "• Auto-sinh link <b>Google Meet</b>, KHÔNG tạo Zoom\n"
    "• Set <b>visibility: private</b> → sếp/đồng nghiệp chỉ thấy busy-block, không xem được nội dung\n"
    "• Không ghi tên John Academy trong mô tả\n"
    "• Vẫn mời được khách tuỳ chọn, recurring hàng tuần OK\n"
    "<i>One-time (chỉ mình chị):</i>\n"
    + _pre(
        'HY "Check-in sức khoẻ":\n'
        "- Thời gian: 25/4/2026 9:00\n"
        "- Thời lượng: 30 phút\n"
        "- Nội dung: Tự review tuần"
    ) + "\n"
    "<i>Recurring + mời khách riêng:</i>\n"
    + _pre(
        'HY "Mentor 1-1 Linh":\n'
        "- Thời gian: 10h sáng thứ 6 hàng tuần trong 8 tuần liên tiếp bắt đầu từ 1/5/2026\n"
        "- Thời lượng: 60 phút\n"
        "- Nội dung: Coaching cá nhân\n"
        "- Khách: linh@abc.com"
    ) + "\n"
    "→ Trong /list hiện dấu 🔒 cạnh tên lịch HY.\n\n"

    "<b>2C. Clone lịch cũ</b> (copy rồi chỉnh, nhanh hơn gõ lại)\n"
    + _pre(
        "tạo lịch giống #5\n"
        "tạo lịch giống #5 nhưng ngày 27/4 15h\n"
        'tạo lịch giống "Tư vấn OKRs" nhưng ngày mai, khách a@x.vn\n'
        'tạo lịch giống #3 nhưng tên "OKRs v2", thêm khách b@y.vn'
    ) + "\n\n"

    # ─── 3. /list
    "📋 <b>3. QUẢN LÝ LỊCH — /list</b>\n\n"

    "<b>3A. Mặc định</b> — " + _code("/list") + " hiện 10 lịch gần nhất:\n"
    "• Mỗi lịch 1 nút số (1-10) → bấm → detail\n"
    "• Detail có nút <b>✏️ Sửa</b> / <b>🗑 Xoá</b>\n"
    "• Menu ✏️: 6 field — giờ/ngày · thời lượng · thêm khách · bỏ khách · tên · nội dung\n"
    "• 🔒 cạnh tên = lịch HY cá nhân\n\n"

    "<b>3B. Tìm &amp; lọc &amp; phân trang</b>\n"
    + _pre(
        "/list 2                   ← trang 2 (10 lịch/trang)\n"
        "/list OKRs                ← lọc từ khoá trong tên/nội dung\n"
        "/list khách lan@abc.com   ← lọc theo email khách\n"
        "/list tuần này\n"
        "/list tuần sau | tuần trước\n"
        "/list hôm nay | mai | hôm qua\n"
        "/list tháng này | tháng 5 | tháng 5/2026\n"
        "/list 27/4                ← ngày cụ thể\n"
        "/list 27/4-4/5            ← khoảng ngày\n"
        "/list OKRs 2              ← từ khoá + trang"
    ) + "\n"

    "<b>3C. Lịch Calendar không do bot tạo</b> 📅\n"
    "Khi /list có lọc theo ngày, bot thêm section \"📅 N lịch từ Calendar\" "
    "với nút <code>E1</code>/<code>E2</code>…\n"
    "• Bấm <code>E#</code> → detail + ✏️ Sửa / 🗑 Xoá luôn qua bot (không cần mở Calendar)\n"
    "• Menu ✏️ 6 field giống lịch bot tạo\n"
    "• Notify-email toggle hoạt động bình thường\n\n"

    "<i>⬇️ Xem tiếp Part 2/2: Sửa nhanh · Lịch lặp · Email · Nhắc · Sync · Icons.</i>"
)


_HELP_TEXT_PART2 = (
    "📖 <b>JA Scheduler Bot — Hướng dẫn (2/2)</b>\n\n"

    # ─── 4. Quick edit
    "⚡ <b>4. SỬA NHANH TỪ CHAT</b> (không cần /list)\n\n"

    "<b>4A. Sửa lịch mới nhất</b> — nhắn thẳng:\n"
    + _pre(
        "sửa giờ 15h30\n"
        "sửa giờ 15h30 25/4/2026\n"
        "sửa thời lượng 45 phút\n"
        "sửa tên Tư vấn OKRs v2\n"
        "sửa nội dung Nội dung mới\n"
        "thêm khách a@x.vn, b@y.vn\n"
        "bỏ khách a@x.vn\n"
        "xoá lịch"
    ) + "\n"

    "<b>4B. Sửa lịch khác — bằng #id</b> (lấy id từ /list):\n"
    + _pre(
        "sửa giờ 15h #5\n"
        "thêm khách a@x.vn #5\n"
        "xoá lịch #5"
    ) + "\n"

    "<b>4C. Sửa lịch cũ bằng TÊN</b> (không cần nhớ id):\n"
    + _pre(
        'sửa giờ 15h30 "Tư vấn OKRs" ngày 25/4\n'
        'xoá lịch "Tư vấn OKRs" ngày 25/4\n'
        "xoá lịch khách lan@abc.com\n"
        'sửa thời lượng 45 phút "Mentor MBOs"'
    ) + "\n"
    "→ Nhiều lịch khớp → bot hiện list để chị bấm số chọn.\n\n"

    # ─── 5. Lịch lặp / 1 buổi riêng
    "🔁 <b>5. LỊCH LẶP — SỬA/XOÁ 1 BUỔI RIÊNG</b>\n"
    "• <b>Sửa 1 buổi:</b> /list → lịch lặp → ✏️ → \"📝 Sửa 1 buổi riêng\" → chọn buổi → Giờ / Thời lượng\n"
    "• <b>Xoá 1 buổi:</b> /list → lịch lặp → 🗑 → \"⦿ Chỉ 1 buổi\" → chọn buổi\n"
    "• <b>Xoá cả series:</b> /list → lịch lặp → 🗑 → \"🗑 Toàn bộ series\"\n\n"

    # ─── 6. Email notify toggle
    "📧 <b>6. GỬI EMAIL CHO KHÁCH HAY KHÔNG</b>\n"
    "Mỗi lần confirm sửa / xoá, bot hiện 2 nút:\n"
    "• <b>✅ … + gửi mail</b> — Google Calendar gửi email update/huỷ cho khách\n"
    "• <b>✅ … (không mail)</b> — update/huỷ âm thầm, khách không nhận email\n"
    "Áp dụng cho: sửa/xoá toàn bộ lịch · sửa/xoá 1 buổi riêng · xoá series · sửa lịch Calendar "
    "không do bot tạo.\n\n"

    # ─── 7. Auto reminder + digest + conflict
    "⏰ <b>7. NHẮC LỊCH &amp; DIGEST TỰ ĐỘNG</b>\n"
    "• <b>~30 phút trước mỗi buổi</b> bot gửi nhắc vào chat này (cả lịch bot tạo + lịch Calendar).\n"
    "• Lịch HY: reminder hiện 🔗 Google Meet thay vì Zoom.\n"
    "• <b>07:00 sáng</b>: digest toàn bộ lịch trong ngày (sort theo giờ, icon 🎯/🔁/📅/🔒).\n"
    "• " + _code("/today") + " — xem digest hôm nay bất kỳ lúc nào.\n\n"

    "⚠️ <b>8. CẢNH BÁO TRÙNG LỊCH</b>\n"
    "Khi tạo / clone / đổi giờ, nếu overlap với lịch khác (kể cả lịch lặp), "
    "bot cảnh báo ngay trong preview. Chị vẫn confirm được nếu cố ý trùng.\n\n"

    # ─── 9. Sync with Calendar drag-drop
    "🔄 <b>9. ĐỒNG BỘ SAU KHI KÉO THẢ TRÊN GOOGLE CALENDAR</b>\n"
    "Chị kéo thoải mái trên Calendar UI. Sau đó báo bot đồng bộ:\n"
    "• " + _code("/sync") + " → đồng bộ lịch mới nhất\n"
    "• " + _code("/sync 5") + " → đồng bộ lịch id=5\n"
    "• Hoặc /list → bấm vào lịch: nếu có drift sẽ có banner ⚠️ + nút <b>🔄 Sync</b>\n"
    "→ Bot coi Calendar là <b>nguồn đúng</b>, update Zoom + DB theo.\n"
    "<i>Lịch HY (Meet) chỉ sync với Calendar, không đụng Zoom.</i>\n\n"

    # ─── Icons legend + notes
    "🎨 <b>10. ICON TRONG /list &amp; DIGEST</b>\n"
    "• 🎯 lịch bot tạo · 🔁 lịch bot tạo + recurring\n"
    "• 🔒 lịch HY cá nhân (Meet, private)\n"
    "• 📅 lịch từ Calendar (không do bot tạo)\n\n"

    # ─── Members directory
    "📇 <b>11. SỔ THÀNH VIÊN CÔNG TY</b>\n"
    "Lưu sẵn email team → khi tạo lịch bấm chọn nhanh thay vì gõ email dài.\n\n"

    "<b>11A. Quản lý sổ</b>\n"
    + _pre(
        "/members                              ← liệt kê sổ\n"
        "/members add lan@abc.com Chị Lan      ← thêm\n"
        "/members add a@x.vn Tên · Chức danh   ← thêm kèm chức danh\n"
        "/members rm lan@abc.com               ← xoá"
    ) + "\n"

    "<b>11B. Trong dòng <code>- Khách:</code> của prompt tạo lịch</b>\n"
    "Có thể dùng tên thay email + shortcut <code>/all</code>:\n"
    + _pre(
        "Khách: Toàn, Hương, Oanh           ← gõ tên trong sổ\n"
        "Khách: /all                        ← cả team (10 người)\n"
        "Khách: /all, abc@external.com      ← cả team + 1 khách ngoài\n"
        "Khách: Toàn, /all, abc@x.vn        ← mix tự do (dedupe)"
    ) + "\n"
    "<i>Alias /all: <code>tất cả</code>, <code>toàn bộ</code>, <code>@all</code>.</i>\n\n"

    "<b>11C. 2 picker trong preview</b>\n"
    "• <b>📇 Thêm từ sổ</b> — mở list members, bấm số TICK thêm. Có "
    "<b>📋 Chọn tất cả</b> / <b>🔄 Bỏ chọn</b> hỗ trợ chọn nhanh.\n"
    "• <b>📋 Sửa danh sách</b> — list TOÀN BỘ khách hiện tại (in/out sổ), "
    "bấm số UNTICK từng người. Có <b>🗑 Bỏ tất cả</b>.\n"
    "<i>Bot tự ẩn nút khi không có tác dụng (sổ đã full → ẩn 📇; chưa có khách → ẩn 📋).</i>\n\n"

    "<b>11D. Workflow gợi ý — mời cả team rồi xoá bớt</b>\n"
    + _pre(
        'Tạo lịch "Họp tuần":\n'
        "- Thời gian: 30/4/2026 14:00\n"
        "- Thời lượng: 60 phút\n"
        "- Nội dung: Sync\n"
        "- Khách: /all"
    ) + "\n"
    "→ Preview 10 khách → 📋 Sửa danh sách → bấm số người vắng → ↩️ Quay lại "
    "→ ✅ Xác nhận tạo.\n\n"

    "⚠️ <b>LƯU Ý</b>\n"
    "• Chỉ chị Hải Yến (chat_id whitelist) nhắn được.\n"
    "• Bot luôn preview trước khi ghi thật. Gõ " + _code("huỷ") + " để bỏ mọi trạng thái chờ.\n"
    "• /sync chỉ đồng bộ cấp series — kéo 1 instance riêng trên Calendar → dùng luồng "
    "\"Sửa 1 buổi riêng\" qua /list."
)


# Phase 3 — Team-only help (cho group "JA Scheduler Team", paste pin).
# Loại admin commands (/list, /list_users, /audit, /members add/rm), HY mode,
# external Calendar — chỉ giữ flow member dùng được.
_TEAM_HELP_TEXT_PART1 = (
    "📖 <b>JA Scheduler Bot — Hướng dẫn team (1/2)</b>\n"
    "<i>Bot ghép Zoom + Google Calendar — phục vụ team JA Scheduler.</i>\n\n"

    "📌 <b>1. LỆNH NHANH</b>\n"
    "• " + _code("/whoami") + " — xem identity của mình\n"
    "• " + _code("/mylist") + " — lịch của mình (xem · sửa · xoá · tìm · lọc)\n"
    "• " + _code("/today") + " — lịch group hôm nay\n"
    "• " + _code("/sync [id]") + " — đồng bộ sau khi kéo thả trên Calendar\n"
    "• " + _code("/members") + " — xem sổ thành viên (gõ tên thay email lúc tạo)\n\n"

    "⚠️ <b>Bot CHỈ phục vụ trong group này. KHÔNG chat 1-1 với bot.</b>\n\n"

    "🆕 <b>2. TẠO LỊCH</b>\n"
    "<i>One-time:</i>\n"
    + _pre(
        'Tạo lịch "Tư vấn JoyClub - Chị Lan":\n'
        "- Thời gian: 22/4/2026 14:00\n"
        "- Thời lượng: 30 phút\n"
        "- Nội dung: Tư vấn gói\n"
        "- Khách: lan@abc.com"
    ) + "\n"
    "<i>Recurring (hàng tuần):</i>\n"
    + _pre(
        'Tạo lịch "Mentor MBOs JoyClub":\n'
        "- Thời gian: 8h30 sáng thứ 4 hàng tuần trong 12 tuần liên tiếp bắt đầu từ 6/5/2026\n"
        "- Thời lượng: 90 phút\n"
        "- Nội dung: Coaching MBOs\n"
        "- Khách: Toàn"
    ) + "\n"
    "→ Bot parse → preview → bấm <b>✅ Xác nhận tạo</b>.\n"
    "💡 <b>Mẹo dòng Khách:</b>\n"
    "• Gõ tên trong sổ thay email: <code>Khách: Toàn, Hương, Oanh</code>\n"
    "• Mời cả team: <code>Khách: /all</code>\n"
    "• Mix tự do: <code>Khách: /all, abc@external.com</code>\n"
    "• Trong preview: bấm <b>📋 Sửa danh sách</b> để bỏ bớt người, "
    "<b>📇 Thêm từ sổ</b> để chọn thêm.\n\n"

    "<b>Clone lịch cũ</b> (copy rồi chỉnh):\n"
    + _pre(
        "tạo lịch giống #5\n"
        "tạo lịch giống #5 nhưng ngày 27/4 15h\n"
        'tạo lịch giống "Tư vấn OKRs" nhưng ngày mai, khách Hương'
    ) + "\n\n"

    "📋 <b>3. QUẢN LÝ LỊCH — /mylist</b>\n"
    "• " + _code("/mylist") + " hiện 10 lịch của BẠN gần nhất\n"
    "• Mỗi lịch 1 nút số → bấm → detail → ✏️ Sửa / 🗑 Xoá\n"
    "• Menu ✏️: 6 field — giờ/ngày · thời lượng · thêm khách · bỏ khách · tên · nội dung\n\n"

    "<b>Tìm &amp; lọc &amp; phân trang</b>\n"
    + _pre(
        "/mylist 2                  ← trang 2\n"
        "/mylist OKRs               ← lọc từ khoá\n"
        "/mylist khách lan@abc.com  ← lọc theo email\n"
        "/mylist tuần này | tuần sau | tuần trước\n"
        "/mylist hôm nay | mai | hôm qua\n"
        "/mylist tháng này | tháng 5\n"
        "/mylist 27/4               ← ngày cụ thể\n"
        "/mylist 27/4-4/5           ← khoảng ngày"
    ) + "\n"
    "<i>⬇️ Xem tiếp Part 2/2: Sửa nhanh · Email · Nhắc · Sync · Icons.</i>"
)


_TEAM_HELP_TEXT_PART2 = (
    "📖 <b>JA Scheduler Bot — Hướng dẫn team (2/2)</b>\n\n"

    "⚡ <b>4. SỬA NHANH TỪ CHAT</b> (không cần /mylist)\n\n"
    "<b>4A. Sửa lịch mới nhất</b> — nhắn thẳng:\n"
    + _pre(
        "sửa giờ 15h30\n"
        "sửa giờ 15h30 25/4/2026\n"
        "sửa thời lượng 45 phút\n"
        "sửa tên Tên mới\n"
        "thêm khách Hương, b@y.vn\n"
        "bỏ khách lan@abc.com\n"
        "xoá lịch"
    ) + "\n"

    "<b>4B. Sửa bằng #id</b> (lấy id từ /mylist):\n"
    + _pre(
        "sửa giờ 15h #5\n"
        "thêm khách Toàn #5\n"
        "xoá lịch #5"
    ) + "\n"

    "<b>4C. Sửa bằng TÊN lịch:</b>\n"
    + _pre(
        'sửa giờ 15h30 "Tư vấn OKRs" ngày 25/4\n'
        "xoá lịch khách lan@abc.com"
    ) + "\n"
    "⚠️ Bạn chỉ sửa/xoá được lịch chính BẠN tạo.\n\n"

    "📧 <b>5. GỬI EMAIL CHO KHÁCH HAY KHÔNG</b>\n"
    "Mỗi lần confirm sửa / xoá, bot hiện 2 nút:\n"
    "• <b>✅ … + gửi mail</b> — Calendar gửi email update/huỷ cho khách\n"
    "• <b>✅ … (không mail)</b> — âm thầm, khách không nhận email\n\n"

    "⏰ <b>6. NHẮC LỊCH TỰ ĐỘNG</b>\n"
    "• <b>~30 phút trước mỗi buổi</b> bot gửi nhắc vào group này, "
    "tag người phụ trách qua @username.\n"
    "• Tạo / sửa / xoá lịch group → bot reply có tag tên + team người tạo.\n"
    "• Muốn không nhận noti → mute group qua Telegram.\n\n"

    "📇 <b>7. SỔ THÀNH VIÊN</b>\n"
    "Sổ có 10 người — gõ tên thay email lúc tạo lịch (đã ghi ở Part 1).\n"
    + _code("/members") + " — xem cả sổ.\n"
    "<i>Thêm người mới vào sổ: ping chị Yến (Admin).</i>\n\n"

    "⚠️ <b>8. CẢNH BÁO TRÙNG LỊCH</b>\n"
    "Khi tạo / clone / đổi giờ, nếu overlap với lịch khác, bot cảnh báo "
    "trong preview. Vẫn confirm được nếu cố ý trùng.\n\n"

    "🔄 <b>9. ĐỒNG BỘ SAU KHI KÉO THẢ TRÊN CALENDAR</b>\n"
    "Kéo event trên Calendar UI → báo bot:\n"
    "• " + _code("/sync") + " → đồng bộ lịch mới nhất\n"
    "• " + _code("/sync 5") + " → đồng bộ lịch id=5\n"
    "→ Bot coi Calendar là <b>nguồn đúng</b>, update Zoom + DB theo.\n\n"

    "🎨 <b>10. ICON TRONG /mylist</b>\n"
    "• 🎯 lịch one-time · 🔁 lịch recurring (hàng tuần)\n\n"

    "⚠️ <b>LƯU Ý</b>\n"
    "• Chỉ thao tác được lịch BẠN tạo (Admin có thể sửa/xoá lịch người khác).\n"
    "• Bot luôn preview trước khi ghi thật. Gõ " + _code("huỷ") + " để bỏ trạng thái chờ.\n"
    "• Mọi vấn đề ping chị Yến trong group."
)


async def _send_help(update: Update) -> None:
    """Help > 4096 chars nên chia 2 tin (Telegram sendMessage giới hạn 4096)."""
    await update.message.reply_text(
        _HELP_TEXT_PART1, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
    )
    await update.message.reply_text(
        _HELP_TEXT_PART2, parse_mode=ParseMode.HTML, disable_web_page_preview=True,
    )


async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    req = await _gate(update, "/start")
    if req is None:
        return
    audit(req, "/start")
    await _send_help(update)


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    req = await _gate(update, "/help")
    if req is None:
        return
    audit(req, "/help")
    await _send_help(update)


async def cmd_today(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """On-demand "today's agenda" — Phase 3: filter theo chat_mode."""
    req = await _gate(update, "/today")
    if req is None:
        return
    from bot import external_events, scheduler  # lazy imports
    now = scheduler._now_vn()
    today = now.date().isoformat()
    items = db.events_on_date(today, chat_mode=req.mode)
    # External Calendar đọc theo calendar_id của mode
    externals = external_events.fetch_on_date(
        now.date(), calendar_id=req.calendar_id or None,
    )
    text = scheduler._format_digest(today, items, externals)
    await update.message.reply_text(
        text, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True,
    )
    audit(req, "/today")


async def cmd_list_users(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """`/list_users` — Admin only. Hiện 3 user trong USERS config."""
    req = await _gate(update, "/list_users")
    if req is None:
        return
    if not req.is_admin:
        msg = "❌ Lệnh này chỉ Admin (Hải Yến) được dùng."
        await update.message.reply_text(msg)
        audit(req, "/list_users", result="reject", error_message="not-admin")
        return
    users = list_user_configs()
    lines = [f"👥 *Danh sách user trong hệ thống* ({len(users)} người):\n"]
    for i, u in enumerate(users, 1):
        role_tag = "Admin" if u.role == "admin" else "Member"
        lines.append(
            f"{i}. *{u.display_name}* ({role_tag})\n"
            f"   Team: {u.team}\n"
            f"   User ID: `{u.user_id}`\n"
            f"   Email: {u.email}"
        )
    lines.append("")
    lines.append("_Để thêm user mới: edit `bot/users_config.py` + push GitHub._")
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN,
    )
    audit(req, "/list_users")


async def cmd_audit(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """`/audit` — Admin only. Sub-commands:
      /audit                  → 20 log gần nhất
      /audit today            → log hôm nay
      /audit user <name>      → log của user (match display_name partial)
      /audit errors           → chỉ result=fail/reject
      /audit warnings         → alias của errors
    """
    req = await _gate(update, "/audit")
    if req is None:
        return
    if not req.is_admin:
        await update.message.reply_text("❌ Lệnh này chỉ Admin được dùng.")
        audit(req, "/audit", result="reject", error_message="not-admin")
        return

    args = ctx.args or []
    sub = (args[0].lower() if args else "")

    kw: dict = {"limit": 20}
    label = "20 log gần nhất"
    if sub == "today":
        from bot import scheduler as _sched
        today = _sched._now_vn().date().isoformat()
        kw["date_from"] = today
        kw["date_to"] = today
        label = f"log hôm nay ({today})"
    elif sub == "user" and len(args) >= 2:
        # Lookup user_id từ name partial
        needle = " ".join(args[1:]).lower()
        user_id = None
        for u in list_user_configs():
            if needle in u.display_name.lower():
                user_id = u.user_id
                label = f"log của {u.display_name}"
                break
        if user_id is None:
            await update.message.reply_text(
                f"⚠️ Không tìm thấy user khớp `{needle}`. "
                f"Dùng /list_users xem tên có sẵn."
            )
            audit(req, "/audit", params=" ".join(args), result="fail",
                  error_message=f"unknown user '{needle}'")
            return
        kw["user_id"] = user_id
    elif sub in ("errors", "warnings", "reject"):
        kw["only_errors"] = True
        label = "log lỗi (fail/reject)"
    elif sub == "":
        pass  # default 20 mới nhất
    else:
        await update.message.reply_text(
            "⚠️ Dùng:\n"
            "• `/audit` — 20 log gần nhất\n"
            "• `/audit today` — log hôm nay\n"
            "• `/audit user <name>` — log theo user\n"
            "• `/audit errors` — chỉ log fail/reject",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    rows = db.query_audit(**kw)
    if not rows:
        await update.message.reply_text(
            f"📭 Không có {label}.", parse_mode=ParseMode.MARKDOWN,
        )
        audit(req, "/audit", params=" ".join(args))
        return

    lines = [f"📜 *Audit log* — {label} ({len(rows)} dòng):\n"]
    for r in rows:
        ts = (r.get("timestamp") or "")[:19].replace("T", " ")
        result = r.get("result") or "?"
        emoji = {"success": "✅", "fail": "❌", "reject": "⛔"}.get(result, "•")
        cmd_str = r.get("command") or "?"
        name = r.get("display_name") or "?"
        mode = r.get("chat_mode") or "?"
        params = r.get("params") or ""
        params_str = f" `{params[:40]}…`" if len(params) > 40 else (f" `{params}`" if params else "")
        err = r.get("error_message") or ""
        err_str = f" — _{err[:60]}_" if err else ""
        lines.append(
            f"{emoji} `{ts}` *{name}* [{mode}] {cmd_str}{params_str}{err_str}"
        )
    # Truncate if quá dài (Telegram limit 4096)
    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3900] + "\n…(truncated)"
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    audit(req, "/audit", params=" ".join(args))


async def cmd_whoami(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Public command — KHÔNG check whitelist. Cho phép TẤT CẢ ai gõ.

    Mục đích: pre-work onboarding member mới (lấy user_id để add vào USERS).
    An toàn: chỉ echo identity của caller, KHÔNG leak data bot/DB/system.

    Phase 3: log vào audit_log với display_name='UNAUTHORIZED_USER' nếu user_id
    không có trong USERS config (Q6 chị xác nhận).
    """
    _log_incoming(update)
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if user is None or chat is None or msg is None:
        return  # impossible cho text command nhưng defensive

    # Phase 3 audit: dùng resolve_context để lấy mode + display, override
    # display_name thành UNAUTHORIZED_USER nếu không có trong USERS
    perm_ctx = resolve_context(update)
    if get_user(user.id) is None:
        audit_display = "UNAUTHORIZED_USER"
        audit_result = "reject"  # đánh dấu probe attempt
        audit_err = f"User {user.id} not in USERS config"
    else:
        audit_display = perm_ctx.display_name
        audit_result = "success"
        audit_err = ""
    db.log_audit(
        user_id=user.id, display_name=audit_display,
        chat_mode=perm_ctx.mode if perm_ctx.mode != "reject" else "unknown",
        command="/whoami",
        params=f"chat_id={chat.id} chat_type={chat.type}",
        result=audit_result, error_message=audit_err,
    )

    # Tên Telegram (last_name + username có thể None)
    name_parts = [user.first_name or ""]
    if user.last_name:
        name_parts.append(user.last_name)
    full_name = " ".join(p for p in name_parts if p) or "(không có tên)"
    username = f"@{user.username}" if user.username else "(không đặt username)"

    # Thời gian VN
    from bot import scheduler as _sched  # lazy import tránh circular
    now = _sched._now_vn()
    ts = now.strftime("%H:%M:%S %d/%m/%Y")

    lines = [
        "🆔 *Thông tin của bạn:*",
        f"👤 User ID: `{user.id}`",
        f"📛 Tên Telegram: {full_name}",
        f"🔗 Username: {username}",
        "",
        "💬 *Thông tin chat hiện tại:*",
        f"📍 Chat ID: `{chat.id}`",
        f"📂 Loại chat: {chat.type}",
    ]
    if chat.title:  # có với group/supergroup/channel, None với private
        lines.append(f"📋 Tên chat: {chat.title}")
    lines.append(f"⏱ Thời gian: {ts}")

    await msg.reply_text(
        "\n".join(lines),
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /list ──────────────────────────────────────────────────────────────────────
async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show paginated / filtered list. Without args → legacy "10 lịch gần nhất".

    Phase 3 permission:
      - Personal: lịch personal của chị (như cũ)
      - Group-Admin: tất cả lịch group
      - Group-Member: REJECT — chỉ /mylist được phép
    """
    req = await _gate(update, "/list")
    if req is None:
        return
    # Phase 3: group member không được /list (chỉ /mylist)
    if req.mode == "group" and not req.is_admin:
        msg = (
            "❌ Bạn chỉ xem được /mylist (lịch của bạn). "
            "Nếu cần xem tất cả, liên hệ Hải Yến (Admin)."
        )
        await update.message.reply_text(msg)
        audit(req, "/list", result="reject", error_message="member-not-admin")
        return

    raw_args = " ".join(ctx.args or []).strip() if hasattr(ctx, "args") else ""
    if not raw_args:
        # Legacy path: no pagination header, preserves exact old UX
        rows = db.list_recent(limit=10, chat_mode=req.mode)
        text = formatter.format_list(rows)
        markup = _list_keyboard(rows)
        ctx.chat_data.pop("list_query", None)
        ctx.chat_data["request_mode"] = req.mode  # remember for callbacks
        ctx.chat_data["request_user_id"] = req.user_id
        await update.message.reply_text(
            text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN
        )
        audit(req, "/list")
        return

    try:
        query = parse_list_args(raw_args)
    except ParseError as e:
        await update.message.reply_text(f"⚠️ {e}")
        audit(req, "/list", params=raw_args, result="fail", error_message=str(e))
        return

    ctx.chat_data["request_mode"] = req.mode
    ctx.chat_data["request_user_id"] = req.user_id
    await _render_list_query(update.message, ctx, query, chat_mode=req.mode)
    audit(req, "/list", params=raw_args)


async def cmd_mylist(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Phase 3 — lịch chính người gọi tạo, theo chat_mode.

    Personal mode: lịch chị Yến trong chat 1-1 (filter chat_mode='personal').
    Group mode: lịch chính user_id hiện tại tạo trong group (filter chat_mode='group',
    created_by_user_id=req.user_id). Member dùng cái này để xem lịch của mình
    khi /list bị admin-only.
    """
    req = await _gate(update, "/mylist")
    if req is None:
        return

    raw_args = " ".join(ctx.args or []).strip() if hasattr(ctx, "args") else ""
    if not raw_args:
        log.info(
            "mylist call: user=%s name=%s mode=%s — filtering by owner+mode",
            req.user_id, req.display_name, req.mode,
        )
        rows = db.list_recent(
            limit=10,
            chat_mode=req.mode,
            created_by_user_id=req.user_id,
        )
        log.info("mylist returned %d rows for user=%s", len(rows), req.user_id)
        # Header rõ ràng — phân biệt /mylist (của bạn) vs /list (admin xem all)
        if rows:
            header = (
                f"📋 *Lịch của {req.display_name}* "
                f"(chế độ {req.mode}, {len(rows)} lịch):\n"
            )
            from bot.formatter import format_event_summary
            lines = [header]
            for i, r in enumerate(rows, 1):
                lines.append(f"{i}. {format_event_summary(r)}")
            text = "\n".join(lines)
        else:
            text = (
                f"📭 *{req.display_name}* chưa tạo lịch nào "
                f"trong chế độ *{req.mode}*."
            )
        markup = _list_keyboard(rows) if rows else None
        ctx.chat_data.pop("list_query", None)
        ctx.chat_data["request_mode"] = req.mode
        ctx.chat_data["request_user_id"] = req.user_id
        ctx.chat_data["mylist_only"] = True
        await update.message.reply_text(
            text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN
        )
        audit(req, "/mylist")
        return

    try:
        query = parse_list_args(raw_args)
    except ParseError as e:
        await update.message.reply_text(f"⚠️ {e}")
        audit(req, "/mylist", params=raw_args, result="fail", error_message=str(e))
        return

    ctx.chat_data["request_mode"] = req.mode
    ctx.chat_data["request_user_id"] = req.user_id
    ctx.chat_data["mylist_only"] = True
    await _render_list_query(
        update.message, ctx, query,
        chat_mode=req.mode, created_by_user_id=req.user_id,
    )
    audit(req, "/mylist", params=raw_args)


async def _render_list_query(message_or_query, ctx: ContextTypes.DEFAULT_TYPE,
                              query, *,
                              chat_mode: str | None = None,
                              created_by_user_id: int | None = None) -> None:
    """Shared renderer for /list + pagination callbacks. Works with Message or CallbackQuery.

    When a date range is specified, also folds in external Calendar events
    (lịch chị Yến tự tạo trên Google Calendar, không do bot tạo). External
    rows are display-only — numbered buttons remain on DB rows.

    Phase 3: optional `chat_mode` + `created_by_user_id` filter.
    """
    # Phase 3 — calendar_id resolve theo mode (default primary)
    cal_id = None
    if chat_mode == "group":
        from bot.permissions import CALENDAR_TEAM_ID
        cal_id = CALENDAR_TEAM_ID() or None
    elif chat_mode == "personal":
        from bot.permissions import CALENDAR_PERSONAL_ID
        cal_id = CALENDAR_PERSONAL_ID()

    externals: list = []
    # External chỉ hiển thị khi /list (không phải /mylist) vì /mylist filter owner
    if (query.date_from and query.date_to
            and created_by_user_id is None):
        try:
            from datetime import date as _date
            externals = external_events.fetch_in_date_range(
                _date.fromisoformat(query.date_from),
                _date.fromisoformat(query.date_to),
                calendar_id=cal_id,
            )
        except Exception:
            log.exception("External fetch failed for /list")
            externals = []

    db_total = db.count_events(
        topic_contains=query.topic,
        attendee_contains=query.attendee,
        date_from=query.date_from,
        date_to=query.date_to,
        chat_mode=chat_mode,
        created_by_user_id=created_by_user_id,
    )
    total = db_total + len(externals)
    total_pages = max(1, (total + query.page_size - 1) // query.page_size)
    if query.page > total_pages:
        query.page = total_pages
    rows = db.search_events(
        topic_contains=query.topic,
        attendee_contains=query.attendee,
        date_from=query.date_from,
        date_to=query.date_to,
        limit=query.page_size,
        offset=query.offset,
        chat_mode=chat_mode,
        created_by_user_id=created_by_user_id,
    )
    externals_shown = externals if query.page == 1 else []
    text = formatter.format_list(
        rows,
        total=total,
        page=query.page,
        page_size=query.page_size,
        query_desc=query.describe_vi(),
        externals=externals_shown or None,
    )
    markup = _paged_list_keyboard(
        rows, query, total_pages, externals=externals_shown or None,
    )

    # Stash current query so pagination callbacks can re-render
    ctx.chat_data["list_query"] = {
        "topic": query.topic,
        "attendee": query.attendee,
        "date_from": query.date_from,
        "date_to": query.date_to,
        "page": query.page,
        "page_size": query.page_size,
    }
    # Stash externals by index so `ext_sel:<idx>` callbacks can resolve.
    # Only page 1 shows externals, so clear on other pages to avoid stale picks.
    if externals_shown:
        ctx.chat_data["list_externals"] = [
            {
                "calendar_event_id": occ.calendar_event_id,
                "occurrence_iso": occ.occurrence_iso,
                "duration_min": occ.duration_min,
                "topic": occ.topic,
                "agenda": occ.agenda,
                "attendees": list(occ.attendees),
                "html_link": occ.html_link,
                "recurring_source_id": occ.recurring_source_id,
            }
            for occ in externals_shown
        ]
    else:
        ctx.chat_data.pop("list_externals", None)

    # message_or_query is either a Message (for /list) or a CallbackQuery (for page nav)
    if hasattr(message_or_query, "edit_message_text"):
        await message_or_query.edit_message_text(
            text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN
        )
    else:
        await message_or_query.reply_text(
            text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN
        )


def _list_keyboard(rows: list[db.EventRow]) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    # Row of numbered buttons, 5 per row
    buttons = [InlineKeyboardButton(str(i), callback_data=f"ls_sel:{r.id}")
               for i, r in enumerate(rows, 1)]
    keyboard = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    return InlineKeyboardMarkup(keyboard)


def _paged_list_keyboard(
    rows: list[db.EventRow], query, total_pages: int,
    externals: list | None = None,
) -> InlineKeyboardMarkup | None:
    """List keyboard with numbered row-pick buttons + page navigation row.

    DB rows get numbered buttons (callback ls_sel:<id>).
    External Calendar rows (page 1 only) get `Eℹ` buttons (callback ext_sel:<idx>).
    """
    if not rows and total_pages <= 1 and not externals:
        return None
    buttons = [InlineKeyboardButton(str(i), callback_data=f"ls_sel:{r.id}")
               for i, r in enumerate(rows, 1)]
    rows_kb = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]

    if externals:
        ext_buttons = [
            InlineKeyboardButton(f"E{i}", callback_data=f"ext_sel:{i - 1}")
            for i in range(1, len(externals) + 1)
        ]
        for i in range(0, len(ext_buttons), 5):
            rows_kb.append(ext_buttons[i:i + 5])

    nav = []
    if query.page > 1:
        nav.append(InlineKeyboardButton("◀ Trước", callback_data=f"lp:{query.page - 1}"))
    nav.append(InlineKeyboardButton(
        f"· {query.page}/{total_pages} ·", callback_data="lp_noop"
    ))
    if query.page < total_pages:
        nav.append(InlineKeyboardButton("Sau ▶", callback_data=f"lp:{query.page + 1}"))
    if nav:
        rows_kb.append(nav)
    return InlineKeyboardMarkup(rows_kb) if rows_kb else None


def _query_from_chat_data(ctx: ContextTypes.DEFAULT_TYPE):
    """Restore a ListQuery from chat_data (for pagination callbacks)."""
    from bot.parser import ListQuery
    d = ctx.chat_data.get("list_query") or {}
    return ListQuery(
        topic=d.get("topic"),
        attendee=d.get("attendee"),
        date_from=d.get("date_from"),
        date_to=d.get("date_to"),
        page=int(d.get("page", 1)),
        page_size=int(d.get("page_size", 10)),
    )


def _detail_keyboard(
    event_id: int, *, show_edit: bool = True, show_sync: bool = False,
) -> InlineKeyboardMarkup:
    rows = []
    if show_edit:
        rows.append([
            InlineKeyboardButton("✏️ Sửa", callback_data=f"ed_menu:{event_id}"),
            InlineKeyboardButton("🗑 Xoá", callback_data=f"del_ask:{event_id}"),
        ])
    if show_sync:
        rows.append([InlineKeyboardButton(
            "🔄 Sync Calendar → Zoom + DB",
            callback_data=f"sync_ask:{event_id}",
        )])
    rows.append([InlineKeyboardButton("↩️ Quay lại list", callback_data="back_list")])
    return InlineKeyboardMarkup(rows)


def _edit_menu_keyboard(event_id: int, *, recurring: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if recurring:
        rows.append([InlineKeyboardButton(
            "🗓 Sửa 1 buổi riêng",
            callback_data=f"ed_occ:{event_id}",
        )])
    rows.extend([
        [
            InlineKeyboardButton("🕐 Giờ/ngày", callback_data=f"ed_f:{event_id}:time"),
            InlineKeyboardButton("⏱ Thời lượng", callback_data=f"ed_f:{event_id}:dur"),
        ],
        [
            InlineKeyboardButton("➕ Thêm khách", callback_data=f"ed_f:{event_id}:att_add"),
            InlineKeyboardButton("➖ Bỏ khách", callback_data=f"ed_f:{event_id}:att_rm"),
        ],
        [
            InlineKeyboardButton("🏷 Tên lịch", callback_data=f"ed_f:{event_id}:topic"),
            InlineKeyboardButton("🎯 Nội dung", callback_data=f"ed_f:{event_id}:ag"),
        ],
        [InlineKeyboardButton("↩️ Quay lại", callback_data=f"back_det:{event_id}")],
    ])
    if recurring:
        rows[1:1] = []  # spacer no-op; kept structure explicit
    return InlineKeyboardMarkup(rows)


def _delete_confirm_keyboard(event_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Xoá + gửi mail", callback_data=f"del_yes:{event_id}:n"),
         InlineKeyboardButton("✅ Xoá (không mail)", callback_data=f"del_yes:{event_id}:s")],
        [InlineKeyboardButton("❌ Huỷ", callback_data=f"back_det:{event_id}")],
    ])


def _edit_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Sửa + gửi mail", callback_data="ed_go:n"),
         InlineKeyboardButton("✅ Sửa (không mail)", callback_data="ed_go:s")],
        [InlineKeyboardButton("❌ Huỷ", callback_data="ed_no")],
    ])


def _delete_occ_confirm_keyboard(event_id: int, idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Xoá + gửi mail", callback_data=f"del_occ_yes:{event_id}:{idx}:n"),
         InlineKeyboardButton("✅ Xoá (không mail)", callback_data=f"del_occ_yes:{event_id}:{idx}:s")],
        [InlineKeyboardButton("❌ Huỷ", callback_data=f"back_det:{event_id}")],
    ])


# ── External (Calendar) edit/delete keyboards ─────────────────────────────────
def _ext_detail_keyboard(idx: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Sửa", callback_data="ext_ed_menu"),
            InlineKeyboardButton("🗑 Xoá", callback_data="ext_del_ask"),
        ],
        [InlineKeyboardButton("↩️ Quay lại list", callback_data="back_list")],
    ])


def _ext_edit_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🕐 Giờ/ngày", callback_data="ext_ed_f:time"),
            InlineKeyboardButton("⏱ Thời lượng", callback_data="ext_ed_f:dur"),
        ],
        [
            InlineKeyboardButton("➕ Thêm khách", callback_data="ext_ed_f:att_add"),
            InlineKeyboardButton("➖ Bỏ khách", callback_data="ext_ed_f:att_rm"),
        ],
        [
            InlineKeyboardButton("🏷 Tên lịch", callback_data="ext_ed_f:topic"),
            InlineKeyboardButton("🎯 Nội dung", callback_data="ext_ed_f:ag"),
        ],
        [InlineKeyboardButton("↩️ Quay lại", callback_data="ext_back")],
    ])


def _ext_edit_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Sửa + gửi mail", callback_data="ext_ed_go:n"),
         InlineKeyboardButton("✅ Sửa (không mail)", callback_data="ext_ed_go:s")],
        [InlineKeyboardButton("❌ Huỷ", callback_data="ext_ed_no")],
    ])


def _ext_delete_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Xoá + gửi mail", callback_data="ext_del_yes:n"),
         InlineKeyboardButton("✅ Xoá (không mail)", callback_data="ext_del_yes:s")],
        [InlineKeyboardButton("❌ Huỷ", callback_data="ext_back")],
    ])


# ── Directory picker (Section 14) ─────────────────────────────────────────────
def _create_preview_keyboard(
    *, has_attendees: bool = False, has_unpicked_in_directory: bool = True,
) -> InlineKeyboardMarkup:
    """Keyboard cho preview tạo lịch — confirm + sổ + (optional) sửa danh sách.

    `has_unpicked_in_directory`: còn member nào trong sổ chưa thuộc danh sách
    khách hay không. False → ẩn nút `📇 Thêm từ sổ` (không có ai để thêm).
    """
    rows = [[
        InlineKeyboardButton("✅ Xác nhận tạo", callback_data="cr_confirm"),
        InlineKeyboardButton("❌ Huỷ", callback_data="cr_cancel"),
    ]]
    secondary = []
    if has_unpicked_in_directory:
        secondary.append(InlineKeyboardButton(
            "📇 Thêm từ sổ", callback_data="dir_open:create"
        ))
    if has_attendees:
        # Phase 14 — review picker: hiện chỉ khi đã có khách để untick
        secondary.append(InlineKeyboardButton(
            "📋 Sửa danh sách", callback_data="rev_open:create"
        ))
    if secondary:
        rows.append(secondary)
    return InlineKeyboardMarkup(rows)


def _preview_keyboard_for(cmd) -> InlineKeyboardMarkup:
    """Helper: compute cả has_attendees + has_unpicked_in_directory từ cmd."""
    attendees_lower = {e.lower() for e in (cmd.attendees or [])}
    has_unpicked = any(
        m.email not in attendees_lower for m in directory.list_members()
    )
    return _create_preview_keyboard(
        has_attendees=bool(cmd.attendees),
        has_unpicked_in_directory=has_unpicked,
    )


def _review_picker_keyboard(attendees: list[str]) -> InlineKeyboardMarkup:
    """Phase 14 — keyboard review/untick picker.

    Layout: [1] [2] [3] [4]... per row of 4
            [🗑 Bỏ tất cả] [↩️ Quay lại preview]
    """
    rows: list[list[InlineKeyboardButton]] = []
    if attendees:
        nums = [
            InlineKeyboardButton(str(i + 1), callback_data=f"rev_rm:{i}")
            for i in range(len(attendees))
        ]
        for i in range(0, len(nums), 4):
            rows.append(nums[i:i + 4])
    bottom = []
    if attendees:
        bottom.append(InlineKeyboardButton(
            "🗑 Bỏ tất cả", callback_data="rev_clear"
        ))
    bottom.append(InlineKeyboardButton(
        "↩️ Quay lại preview", callback_data="rev_back"
    ))
    rows.append(bottom)
    return InlineKeyboardMarkup(rows)


def _att_add_prompt_keyboard(*, kind: str, event_id: int | None = None) -> InlineKeyboardMarkup:
    """Đi kèm prompt 'Nhắn email cần THÊM' → cho phép mở sổ thay vì gõ tay.

    `kind` = 'edit' (lịch bot tạo) | 'ext' (lịch external).
    """
    if kind == "edit" and event_id is not None:
        cb = f"dir_open:edit:{event_id}"
    else:
        cb = "dir_open:ext"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📇 Sổ thành viên", callback_data=cb)],
    ])


def _directory_keyboard(
    *,
    members_on_page: list,
    base_index: int,
    page: int,
    total_pages: int,
    has_selection: bool,
    has_unpicked: bool,
) -> InlineKeyboardMarkup:
    """Inline keyboard cho directory panel.

    Layout:
      [1] [2] [3] [4]
      [5] [6] [7] [8]
      [📋 Chọn tất cả]  [🔄 Bỏ chọn]
      [◀] [page/total] [▶]   (nếu nhiều trang)
      [✅ Xong] [❌ Huỷ]
    """
    rows: list[list[InlineKeyboardButton]] = []
    if members_on_page:
        nums = [
            InlineKeyboardButton(
                str(base_index + i + 1),
                callback_data=f"dir_t:{base_index + i}",
            )
            for i in range(len(members_on_page))
        ]
        for i in range(0, len(nums), 4):
            rows.append(nums[i:i + 4])

    # Bulk actions — áp dụng cho TOÀN BỘ sổ, không chỉ trang hiện tại
    bulk_row = []
    if has_unpicked:
        bulk_row.append(InlineKeyboardButton(
            "📋 Chọn tất cả", callback_data="dir_all"
        ))
    if has_selection:
        bulk_row.append(InlineKeyboardButton(
            "🔄 Bỏ chọn", callback_data="dir_clear"
        ))
    if bulk_row:
        rows.append(bulk_row)

    if total_pages > 1:
        nav = []
        if page > 1:
            nav.append(InlineKeyboardButton("◀", callback_data=f"dir_p:{page - 1}"))
        nav.append(InlineKeyboardButton(
            f"· {page}/{total_pages} ·", callback_data="dir_noop"
        ))
        if page < total_pages:
            nav.append(InlineKeyboardButton("▶", callback_data=f"dir_p:{page + 1}"))
        rows.append(nav)
    done_label = "✅ Xong" if has_selection else "✅ Đóng"
    rows.append([
        InlineKeyboardButton(done_label, callback_data="dir_done"),
        InlineKeyboardButton("❌ Huỷ", callback_data="dir_cancel"),
    ])
    return InlineKeyboardMarkup(rows)


def _base_attendees_for_dir_mode(ctx: ContextTypes.DEFAULT_TYPE) -> list[str]:
    """Pull current attendees từ pending state tuỳ kind — để hiển thị '✓' / dedupe merge."""
    dm = ctx.chat_data.get("dir_mode") or {}
    kind = dm.get("kind")
    if kind == "create":
        cmd = ctx.chat_data.get("pending")
        if cmd:
            return list(cmd.attendees or [])
    elif kind == "edit_add":
        eid = dm.get("event_id")
        if eid:
            row = db.get_event(int(eid))
            if row:
                return list(row.attendees or [])
    elif kind == "ext_add":
        occ = ctx.chat_data.get("current_ext")
        if occ:
            return list(occ.get("attendees") or [])
    return []


async def _render_directory_panel(query, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Edit current message thành directory panel theo state hiện tại."""
    dm = ctx.chat_data.get("dir_mode")
    if not dm:
        await query.edit_message_text(
            "⚠️ Phiên chọn sổ đã hết hạn. Mở lại từ preview tạo lịch giúp em."
        )
        return
    page = int(dm.get("page", 1))
    members_on_page, total_pages, page = directory.page_slice(page)
    dm["page"] = page  # clamp & persist
    base = {e.lower() for e in _base_attendees_for_dir_mode(ctx)}
    selected = set(dm.get("selected_emails") or [])
    text = formatter.format_directory_panel(
        members_on_page=members_on_page,
        page=page,
        total_pages=total_pages,
        selected_emails=selected,
        base_emails=base,
        base_index=(page - 1) * directory.PAGE_SIZE,
        kind=dm.get("kind", "create"),
    )
    has_pick = any(e not in base for e in selected)
    # Unpicked = thành viên TOÀN SỔ chưa được tick (selected) và chưa thuộc base
    all_emails = {m.email for m in directory.list_members()}
    has_unpicked = any(
        e not in selected and e not in base for e in all_emails
    )
    markup = _directory_keyboard(
        members_on_page=members_on_page,
        base_index=(page - 1) * directory.PAGE_SIZE,
        page=page,
        total_pages=total_pages,
        has_selection=has_pick,
        has_unpicked=has_unpicked,
    )
    await query.edit_message_text(
        text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN
    )


def _enter_dir_mode(
    ctx: ContextTypes.DEFAULT_TYPE,
    *,
    kind: str,
    event_id: int | None = None,
) -> None:
    """Khởi tạo state dir_mode. selected_emails khởi tạo từ base attendees để
    người chọn thấy ngay '✓' của những người vốn đã có trong lịch."""
    base = []
    if kind == "create":
        cmd = ctx.chat_data.get("pending")
        if cmd:
            base = list(cmd.attendees or [])
    elif kind == "edit_add" and event_id is not None:
        row = db.get_event(int(event_id))
        if row:
            base = list(row.attendees or [])
    elif kind == "ext_add":
        occ = ctx.chat_data.get("current_ext") or {}
        base = list(occ.get("attendees") or [])
    ctx.chat_data["dir_mode"] = {
        "kind": kind,
        "page": 1,
        "selected_emails": [e.lower() for e in base],
        "event_id": int(event_id) if event_id is not None else None,
    }


async def _exit_dir_mode_back_to_create(
    query, ctx: ContextTypes.DEFAULT_TYPE, *, apply: bool,
) -> None:
    """Đóng directory mode, quay về preview tạo lịch (kind='create')."""
    dm = ctx.chat_data.pop("dir_mode", None) or {}
    cmd = ctx.chat_data.get("pending")
    if cmd is None:
        await query.edit_message_text(
            "⚠️ Phiên tạo lịch đã hết hạn. Gửi lại lệnh tạo giúp em nhé."
        )
        return
    if apply:
        base_lower = {e.lower() for e in (cmd.attendees or [])}
        picked_new = [
            e for e in dm.get("selected_emails", [])
            if e.lower() not in base_lower
        ]
        if picked_new:
            cmd.attendees = list(dict.fromkeys([*cmd.attendees, *picked_new]))
            ctx.chat_data["pending"] = cmd
    conflicts = _collect_conflicts_for(cmd)
    preview = (
        formatter.format_confirm_preview(cmd)
        + formatter.format_conflict_warning(conflicts)
    )
    await query.edit_message_text(
        preview,
        reply_markup=_preview_keyboard_for(cmd),
        parse_mode=ParseMode.MARKDOWN,
    )


async def _exit_dir_mode_to_edit_add(
    query, ctx: ContextTypes.DEFAULT_TYPE, *, apply: bool,
) -> None:
    """Đóng directory mode, đi vào confirm-edit flow cho field 'att_add'."""
    dm = ctx.chat_data.pop("dir_mode", None) or {}
    event_id = dm.get("event_id")
    if event_id is None:
        await query.edit_message_text("⚠️ Phiên sửa đã hết hạn.")
        return
    row = db.get_event(int(event_id))
    if not row or row.status != "active":
        await query.edit_message_text("⚠️ Lịch không còn tồn tại.")
        return
    if not apply:
        # Quay lại edit menu — chị có thể bấm field khác
        await query.edit_message_text(
            f"❌ Đã huỷ chọn sổ.\n\n✏️ Sửa lịch id={event_id}: *{row.topic}*\nChọn field:",
            reply_markup=_edit_menu_keyboard(event_id, recurring=bool(row.recurring)),
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    base_lower = {e.lower() for e in (row.attendees or [])}
    picked_new = [
        e for e in dm.get("selected_emails", []) if e.lower() not in base_lower
    ]
    if not picked_new:
        await query.edit_message_text(
            "⚠️ Chị chưa chọn ai mới (những người tick sẵn là khách đã có trong lịch). "
            "Mở lại sổ và chọn người mới giúp em nhé.",
            reply_markup=_edit_menu_keyboard(event_id, recurring=bool(row.recurring)),
        )
        return
    try:
        new_value, display = _parse_edit(row, "att_add", ", ".join(picked_new))
    except ParseError as e:
        await query.edit_message_text(f"⚠️ {e}")
        return
    ctx.chat_data["pending_edit"] = {
        "event_id": event_id,
        "field": "att_add",
        "new_value": new_value,
        "display": display,
    }
    preview = formatter.format_edit_preview(row, "att_add", display)
    await query.edit_message_text(
        preview,
        reply_markup=_edit_confirm_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


async def _exit_dir_mode_to_ext_add(
    query, ctx: ContextTypes.DEFAULT_TYPE, *, apply: bool,
) -> None:
    """Đóng directory mode, đi vào confirm-ext-edit cho 'att_add'."""
    dm = ctx.chat_data.pop("dir_mode", None) or {}
    occ = ctx.chat_data.get("current_ext")
    if not occ:
        await query.edit_message_text("⚠️ Không còn dữ liệu lịch Calendar.")
        return
    if not apply:
        await query.edit_message_text(
            f"❌ Đã huỷ chọn sổ.\n\n✏️ Sửa lịch Calendar *{occ['topic']}*\nChọn field:",
            reply_markup=_ext_edit_menu_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    base_lower = {e.lower() for e in (occ.get("attendees") or [])}
    picked_new = [
        e for e in dm.get("selected_emails", []) if e.lower() not in base_lower
    ]
    if not picked_new:
        await query.edit_message_text(
            "⚠️ Chị chưa chọn ai mới (những người tick sẵn là khách đã có trong lịch).",
            reply_markup=_ext_edit_menu_keyboard(),
        )
        return
    try:
        new_value, display = _parse_ext_edit(occ, "att_add", ", ".join(picked_new))
    except ParseError as e:
        await query.edit_message_text(f"⚠️ {e}")
        return
    ctx.chat_data["pending_ext_edit"] = {
        "field": "att_add", "new_value": new_value, "display": display,
    }
    preview = formatter.format_external_edit_preview(occ, "att_add", display)
    await query.edit_message_text(
        preview,
        reply_markup=_ext_edit_confirm_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /members command ──────────────────────────────────────────────────────────
async def cmd_members(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """`/members` — xem / thêm / xoá thành viên trong sổ.

    Phase 3 permission:
      - list (no args): ai cũng xem được (personal + admin + member)
      - add/rm/edit: CHỈ admin (group-member bị reject với hint)
    """
    req = await _gate(update, "/members")
    if req is None:
        return
    args = ctx.args or []
    sub = (args[0].lower() if args else "")
    is_modify = sub in ("add", "rm", "remove", "delete", "del", "xoá", "xoa")
    if is_modify and not req.is_admin:
        msg = (
            "❌ Chỉ Admin (Hải Yến) được modify sổ. "
            "Bạn xem sổ bằng `/members` (no args)."
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)
        audit(req, "/members", params=" ".join(args), result="reject",
              error_message="member-not-admin")
        return

    if not args:
        members = directory.list_members()
        if not members:
            await update.message.reply_text(
                "📭 Sổ thành viên trống.\n\n"
                "Thêm bằng:\n"
                "  `/members add <email> <tên>`\n"
                "  `/members add lan@abc.com Chị Lan`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        lines = [f"📇 *Sổ thành viên công ty* ({len(members)} người):\n"]
        for i, m in enumerate(members, 1):
            title = f" · {m.title}" if m.title else ""
            lines.append(f"{i}. *{m.name}*{title} · `{m.email}`")
        lines.append("")
        lines.append("_Thêm: `/members add <email> <tên>`_")
        lines.append("_Xoá: `/members rm <email>`_")
        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN
        )
        return

    if sub == "add":
        if len(args) < 3:
            await update.message.reply_text(
                "⚠️ Format: `/members add <email> <tên>` "
                "(VD: `/members add lan@abc.com Chị Lan`)",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        email = args[1]
        # Phần còn lại = tên (cho phép có dấu cách trong tên)
        rest = " ".join(args[2:]).strip()
        # Cho phép format "Name · Title" để tách title (tuỳ chọn)
        if " · " in rest:
            name, _, title = rest.partition(" · ")
        else:
            name, title = rest, ""
        try:
            m = directory.add_member(name=name.strip(), email=email, title=title.strip())
        except ValueError as e:
            await update.message.reply_text(f"⚠️ {e}")
            return
        await update.message.reply_text(
            f"✅ Đã thêm: *{m.name}* · `{m.email}`"
            + (f" · {m.title}" if m.title else "")
            + f"\n\nSổ hiện có {len(directory.list_members())} người.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if sub in ("rm", "remove", "delete", "del", "xoá", "xoa"):
        if len(args) < 2:
            await update.message.reply_text(
                "⚠️ Format: `/members rm <email>`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        email = args[1]
        ok = directory.remove_member(email)
        if ok:
            await update.message.reply_text(
                f"🗑 Đã xoá `{email}` khỏi sổ.\n"
                f"Sổ còn {len(directory.list_members())} người.",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text(
                f"⚠️ Không tìm thấy `{email}` trong sổ.",
                parse_mode=ParseMode.MARKDOWN,
            )
        return

    await update.message.reply_text(
        "⚠️ Dùng:\n"
        "• `/members` — xem sổ\n"
        "• `/members add <email> <tên>` — thêm\n"
        "• `/members rm <email>` — xoá",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Text handler: create-parse OR edit-value (depending on state) ─────────────
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    req = await _gate(update, "text")
    if req is None:
        return
    # Stash request context cho create flow + callbacks dùng
    ctx.chat_data["request_mode"] = req.mode
    ctx.chat_data["request_user_id"] = req.user_id
    ctx.chat_data["request_display_name"] = req.display_name
    text = update.message.text or ""
    low = text.strip().lower()

    # Phase 3.x (2026-05-06): Group silent mode — trong group, em chỉ reply khi
    # có lý do rõ ràng (lệnh tạo lịch, đang ở pending state, hoặc bị tag).
    # Chat phiếm khác trong group → silent return (không spam).
    # Cá nhân 1-1 vẫn reply mọi text như cũ.
    is_group = req.mode == "group"
    bot_addressed = _is_bot_addressed(update, ctx) if is_group else True
    had_pending = _has_pending_state(ctx.chat_data)

    # 0) Escape hatch: "huỷ" / "hủy" / "bỏ" / "cancel" clears any pending state.
    if low in {"huỷ", "hủy", "bỏ", "bo", "huy", "cancel", "/cancel"}:
        for k in _PENDING_STATE_KEYS:
            ctx.chat_data.pop(k, None)
        # Group + không có pending + không tag bot → silent (tránh spam khi
        # ai đó tình cờ gõ "huỷ" trong cuộc trò chuyện khác).
        if is_group and not had_pending and not bot_addressed:
            return
        await update.message.reply_text(
            "✅ Đã huỷ trạng thái chờ." if had_pending else "ℹ️ Không có gì đang chờ."
        )
        return

    # 1) Edit-value mode takes priority (chị đang điền giá trị mới cho field)
    edit_mode = ctx.chat_data.get("edit_mode")
    if edit_mode:
        await _handle_edit_value(update, ctx, text, edit_mode)
        return
    ext_edit_mode = ctx.chat_data.get("ext_edit_mode")
    if ext_edit_mode:
        await _handle_ext_edit_value(update, ctx, text, ext_edit_mode)
        return

    # 2) Quick-edit supersedes any pending_edit (newer command wins — better UX
    #    than forcing chị phải bấm nút cũ).
    try:
        quick = parse_quick_edit(text)
    except ParseError as e:
        # Group + không tag bot + không có pending → có thể chỉ là text trùng
        # pattern quick-edit nhưng không phải lệnh thật → silent.
        if is_group and not bot_addressed and not had_pending:
            return
        await update.message.reply_text(f"⚠️ {e}")
        return
    if quick is not None:
        ctx.chat_data.pop("pending_edit", None)
        ctx.chat_data.pop("pending_delete", None)
        await _handle_quick_edit(update, ctx, *quick)
        return

    # 3) Pending-edit blocks only non-quick-edit free text
    if ctx.chat_data.get("pending_edit") or ctx.chat_data.get("pending_ext_edit"):
        await update.message.reply_text(
            "⚠️ Đang chờ xác nhận sửa. Bấm ✅/❌, nhắn lệnh sửa mới, hoặc nhắn `huỷ`."
        )
        return

    # 4) Clone flow must run BEFORE the generic create flow — "tạo lịch giống #5"
    #    would otherwise be swallowed by parse_command with garbage topic.
    try:
        clone = parse_clone(text)
    except ParseError as e:
        if is_group and not bot_addressed and not had_pending:
            return  # silent — tránh spam
        await update.message.reply_text(f"⚠️ {e}")
        return
    if clone is not None:
        await _handle_clone(update, ctx, clone)
        return

    # 5) Default: create flow — accept "tạo lịch" (work) or "HY" prefix (cá nhân)
    # Phase 3: HY chỉ trong personal mode (chỉ chị Yến).
    if is_personal_prefix(text) and req.mode != "personal":
        await update.message.reply_text(
            "❌ Lệnh `HY` (lịch cá nhân private) chỉ dùng trong chat 1-1 với bot. "
            "Trong group hãy dùng `Tạo lịch` thường.",
            parse_mode=ParseMode.MARKDOWN,
        )
        audit(req, "HY", result="reject", error_message="HY trong group")
        return
    low_txt = text.lower()
    is_hy = is_personal_prefix(text)
    is_create_attempt = (
        "tạo lịch" in low_txt or "tao lich" in low_txt or is_hy
    )
    if not is_create_attempt:
        # Group: chat phiếm — silent. Trừ khi bị tag bot trực tiếp → reply
        # hướng dẫn tóm tắt để hỗ trợ thành viên mới.
        if is_group:
            if bot_addressed:
                await update.message.reply_text(
                    _GROUP_MENTION_HELP,
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                )
            return
        # Cá nhân 1-1: vẫn reply hint như cũ
        await update.message.reply_text(
            f"Em chưa hiểu. Gõ /help để xem ví dụ hoặc {_list_cmd_for(ctx.chat_data)} để quản lý lịch cũ."
        )
        return

    try:
        cmd = parse_command(text)
    except ParseError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return
    except Exception:
        log.exception("Unexpected parser error")
        await update.message.reply_text("❌ Lỗi parser. Em check log.")
        return

    # Phase 13 — resolve tên thành email từ sổ (chị có thể gõ "Lan" thay
    # "lan@abc.com" trong dòng "- Khách:"). Không match → giữ warning trong preview.
    _resolve_attendees_into_cmd(cmd)

    ctx.chat_data["pending"] = cmd
    conflicts = _collect_conflicts_for(cmd)
    preview = (
        formatter.format_confirm_preview(cmd)
        + formatter.format_conflict_warning(conflicts)
    )
    await update.message.reply_text(
        preview,
        reply_markup=_preview_keyboard_for(cmd),
        parse_mode=ParseMode.MARKDOWN,
    )


def _resolve_attendees_into_cmd(cmd: ParsedCommand) -> None:
    """Ghi đè cmd.attendees bằng kết quả resolve qua sổ thành viên (Phase 13).

    Idempotent: nếu raw_labels không có 'attendees' thì không làm gì.
    """
    raw = (cmd.raw_labels or {}).get("attendees") if hasattr(cmd, "raw_labels") else None
    if not raw:
        return
    try:
        resolved, problems = directory.resolve_attendees_line(raw)
    except Exception:
        log.exception("resolve_attendees_line fail — giữ list cũ")
        return
    cmd.attendees = resolved
    cmd.attendees_problems = [p.error for p in problems if p.error]


# ── Clone flow ────────────────────────────────────────────────────────────────
async def _handle_clone(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE, clone: CloneSpec
) -> None:
    """Resolve source lịch, apply overrides, route into the standard create preview."""
    sources = _resolve_targets(ctx, clone.target)
    if not sources:
        await update.message.reply_text(
            "⚠️ Không tìm thấy lịch nguồn để clone. "
            "Thử `#id` hoặc thêm `\"tên lịch\"` / `ngày DD/MM`."
        )
        return
    if len(sources) > 1:
        ctx.chat_data["pending_clone_disambig"] = {
            "overrides": _overrides_to_dict(clone.overrides),
            "candidate_ids": [r.id for r in sources],
        }
        text = formatter.format_candidate_list(sources, "clone")
        buttons = [
            InlineKeyboardButton(str(i), callback_data=f"clone_sel:{r.id}")
            for i, r in enumerate(sources, 1)
        ]
        rows_kb = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
        rows_kb.append([InlineKeyboardButton("❌ Huỷ", callback_data="clone_cancel")])
        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(rows_kb),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await _preview_clone(update.effective_message, ctx, sources[0], clone.overrides)


def _overrides_to_dict(ov: CloneOverrides) -> dict:
    return {
        "topic": ov.topic,
        "start_date": ov.start_date.isoformat() if ov.start_date else None,
        "start_time": list(ov.start_time) if ov.start_time else None,
        "duration_min": ov.duration_min,
        "agenda": ov.agenda,
        "attendees_replace": ov.attendees_replace,
        "attendees_add": ov.attendees_add,
    }


def _dict_to_overrides(d: dict) -> CloneOverrides:
    from datetime import date as _date
    return CloneOverrides(
        topic=d.get("topic"),
        start_date=_date.fromisoformat(d["start_date"]) if d.get("start_date") else None,
        start_time=tuple(d["start_time"]) if d.get("start_time") else None,
        duration_min=d.get("duration_min"),
        agenda=d.get("agenda"),
        attendees_replace=d.get("attendees_replace"),
        attendees_add=d.get("attendees_add"),
    )


def _build_clone_command(source: db.EventRow, ov: CloneOverrides) -> ParsedCommand:
    """Merge source row + overrides into a fresh ParsedCommand.

    Recurrence is dropped by default — a clone is a one-off lịch at the new time.
    If chị needs another recurring series she can type one from scratch.
    """
    base = source.start_dt
    new_date = ov.start_date or base.date()
    hour, minute = (ov.start_time if ov.start_time else (base.hour, base.minute))
    start = datetime(new_date.year, new_date.month, new_date.day, hour, minute)

    attendees = list(source.attendees)
    if ov.attendees_replace is not None:
        attendees = list(ov.attendees_replace)
    if ov.attendees_add:
        attendees = list(dict.fromkeys([*attendees, *ov.attendees_add]))

    return ParsedCommand(
        topic=ov.topic or source.topic,
        start=start,
        duration_min=ov.duration_min or source.duration_min,
        agenda=ov.agenda if ov.agenda is not None else source.agenda,
        attendees=attendees,
        recurring=None,
    )


async def _preview_clone(
    reply_target,
    ctx: ContextTypes.DEFAULT_TYPE,
    source: db.EventRow,
    ov: CloneOverrides,
) -> None:
    cmd = _build_clone_command(source, ov)
    ctx.chat_data["pending"] = cmd
    conflicts = _collect_conflicts_for(cmd)
    header = (
        f"📋 *Clone lịch #{source.id}* "
        f"({source.topic}) — bản mới sẽ là:\n\n"
    )
    preview = header + formatter.format_confirm_preview(cmd) + formatter.format_conflict_warning(conflicts)
    keyboard = _preview_keyboard_for(cmd)
    if hasattr(reply_target, "edit_message_text"):
        await reply_target.edit_message_text(
            preview, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN
        )
    else:
        await reply_target.reply_text(
            preview, reply_markup=keyboard, parse_mode=ParseMode.MARKDOWN
        )


def _collect_conflicts_for(
    cmd: ParsedCommand, *, exclude_id: int | None = None
) -> list[tuple[db.EventRow, str]]:
    """Find overlaps for every occurrence of `cmd`. Deduped by conflicting-event id."""
    occurrences = [cmd.start]
    if cmd.recurring:
        count = int(cmd.recurring.get("count", 1))
        occurrences = [cmd.start + timedelta(weeks=i) for i in range(count)]

    seen_ids: set[int] = set()
    hits: list[tuple[db.EventRow, str]] = []
    for occ_start in occurrences:
        occ_iso = occ_start.isoformat(timespec="seconds")
        for ev, conflict_iso in db.find_conflicts(
            occ_iso, cmd.duration_min, exclude_id=exclude_id
        ):
            if ev.id in seen_ids:
                continue
            seen_ids.add(ev.id)
            hits.append((ev, conflict_iso))
    return hits


async def _handle_edit_value(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    text: str,
    edit_mode: dict,
) -> None:
    event_id = edit_mode["event_id"]
    field = edit_mode["field"]
    row = db.get_event(event_id)
    if row is None or row.status != "active":
        ctx.chat_data.pop("edit_mode", None)
        await update.message.reply_text(
            f"⚠️ Lịch này không còn tồn tại. Gõ {_list_cmd_for(ctx.chat_data)} để xem."
        )
        return

    try:
        new_value, display = _parse_edit(row, field, text)
    except ParseError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return

    ctx.chat_data.pop("edit_mode", None)
    occ_idx = edit_mode.get("occurrence_idx")
    ctx.chat_data["pending_edit"] = {
        "event_id": event_id,
        "field": field,
        "new_value": new_value,
        "display": display,
        "occurrence_idx": occ_idx,
    }
    if occ_idx is not None:
        occs = ctx.chat_data.get("occurrences", {}).get(event_id, [])
        if occ_idx >= len(occs):
            await update.message.reply_text(
                f"⚠️ Danh sách buổi đã cũ. Gõ {_list_cmd_for(ctx.chat_data)} làm lại."
            )
            ctx.chat_data.pop("pending_edit", None)
            return
        preview = formatter.format_occurrence_preview(
            row, occs[occ_idx], field, display
        )
    else:
        preview = formatter.format_edit_preview(row, field, display)
        if field == "time":
            preview += formatter.format_conflict_warning(
                _conflicts_for_time_edit(row, new_value)
            )
    await update.message.reply_text(
        preview,
        reply_markup=_edit_confirm_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


def _parse_edit(row: db.EventRow, field: str, text: str):
    """Return (new_value, human_display_string).

    Phase 13: với att_add / att_rm, chấp nhận tên member trong sổ thay email.
    """
    if field == "time":
        dt = parse_edit_time(text, base=row.start_dt)
        return dt.isoformat(timespec="seconds"), f"🕐 {dt.day}/{dt.month}/{dt.year} {dt.hour:02d}:{dt.minute:02d}"
    if field == "dur":
        n = parse_edit_duration(text)
        return n, f"⏱ {n} phút"
    if field == "att_add":
        emails = _resolve_attendees_for_edit(text)
        merged = list(dict.fromkeys([*row.attendees, *emails]))
        added = [e for e in emails if e not in row.attendees]
        if not added:
            raise ParseError("Các email này đã có trong lịch rồi.")
        return merged, "➕ Thêm: " + ", ".join(added) + f"\n→ Sau khi sửa: {len(merged)} khách"
    if field == "att_rm":
        emails = _resolve_attendees_for_edit(text)
        remaining = [e for e in row.attendees if e not in emails]
        removed = [e for e in emails if e in row.attendees]
        if not removed:
            raise ParseError("Không thấy email nào trong list để bỏ.")
        return remaining, "➖ Bỏ: " + ", ".join(removed) + f"\n→ Còn lại: {len(remaining)} khách"
    if field == "topic":
        v = parse_edit_plain(text, label="Tên lịch")
        return v, f"🏷 {v}"
    if field == "ag":
        v = parse_edit_plain(text, label="Nội dung")
        return v, f"🎯 {v}"
    raise ParseError(f"Field không hợp lệ: {field}")


def _resolve_attendees_for_edit(text: str) -> list[str]:
    """Phase 13 — resolve text input (cả tên lẫn email) thành email list.

    Raises ParseError nếu có token không match được, để user thấy ngay.
    """
    try:
        resolved, problems = directory.resolve_attendees_line(text)
    except Exception:
        log.exception("resolve_attendees_line fail")
        # Fallback về regex-only
        return parse_edit_emails(text)
    if problems:
        lines = ["Em không hiểu một số tên:"]
        for p in problems:
            if p.error:
                lines.append(f"  • {p.error}")
        lines.append("Chị nhắn lại bằng email đầy đủ hoặc /members add trước.")
        raise ParseError("\n".join(lines))
    if not resolved:
        raise ParseError("Em không tìm thấy email nào. Nhắn dạng `a@x.vn, b@y.vn` hoặc tên trong sổ.")
    return resolved


# ── Drag-drop sync: pull Calendar state into DB + Zoom ────────────────────────
_TITLE_PREFIX = "[John Academy] "


def _parse_cal_datetime(s: str) -> datetime:
    """Strip timezone offset from 'YYYY-MM-DDTHH:MM:SS+07:00' → naive local."""
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=None)


def compute_drift(row: db.EventRow) -> dict:
    """Return dict of {field: (db_value, cal_value)} for fields out of sync."""
    event = _get_calendar().get_event(
        row.calendar_event_id, calendar_id=_calendar_id_for_row(row),
    )
    diffs: dict = {}

    cal_start_str = event.get("start", {}).get("dateTime")
    cal_end_str = event.get("end", {}).get("dateTime")
    if cal_start_str and cal_end_str:
        cal_start = _parse_cal_datetime(cal_start_str)
        cal_end = _parse_cal_datetime(cal_end_str)
        cal_start_iso = cal_start.isoformat(timespec="seconds")
        if cal_start_iso != row.start_local:
            diffs["start"] = (row.start_local, cal_start_iso)
        cal_duration = int((cal_end - cal_start).total_seconds() // 60)
        if cal_duration != row.duration_min:
            diffs["duration"] = (row.duration_min, cal_duration)

    cal_topic = (event.get("summary") or "").removeprefix(_TITLE_PREFIX).strip()
    if cal_topic and cal_topic != row.topic:
        diffs["topic"] = (row.topic, cal_topic)

    cal_attendees = sorted(
        a["email"] for a in event.get("attendees", []) if a.get("email")
    )
    db_attendees = sorted(row.attendees)
    if cal_attendees != db_attendees:
        diffs["attendees"] = (db_attendees, cal_attendees)

    return diffs


def format_drift(diffs: dict) -> str:
    if not diffs:
        return "✅ Lịch đang đồng bộ với Calendar."
    lines = ["🔍 *Phát hiện lệch giữa Calendar ↔ bot:*\n"]
    labels = {
        "start": "🕐 Giờ bắt đầu",
        "duration": "⏱ Thời lượng (phút)",
        "topic": "🏷 Tên lịch",
        "attendees": "👥 Khách",
    }
    for field, (db_v, cal_v) in diffs.items():
        label = labels.get(field, field)
        lines.append(f"{label}:")
        lines.append(f"  • DB: `{db_v}`")
        lines.append(f"  • Calendar: `{cal_v}`")
    return "\n".join(lines)


def _apply_drift_sync(row: db.EventRow, diffs: dict) -> None:
    """Apply Calendar → Zoom + DB (Calendar is source of truth)."""
    updates: dict = {}
    zoom_kwargs: dict = {}

    if "start" in diffs:
        new_start = diffs["start"][1]
        updates["start_local"] = new_start
        zoom_kwargs["start_local_iso"] = new_start
    if "duration" in diffs:
        new_dur = diffs["duration"][1]
        updates["duration_min"] = new_dur
        zoom_kwargs["duration_min"] = new_dur
    if "topic" in diffs:
        new_topic = diffs["topic"][1]
        updates["topic"] = new_topic
        zoom_kwargs["topic"] = new_topic
    if "attendees" in diffs:
        updates["attendees"] = diffs["attendees"][1]

    # Zoom: push any time/duration/topic change (skip for HY — no Zoom backing)
    if zoom_kwargs and row.provider != "meet":
        _zoom.update_meeting(row.zoom_meeting_id, **zoom_kwargs)

    # DB: last-write-wins
    if updates:
        db.update_event_fields(row.id, **updates)


async def cmd_sync(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    req = await _gate(update, "/sync")
    if req is None:
        return
    args = ctx.args or []
    target_id: int | None = None
    if args:
        raw = args[0].lstrip("#")
        if raw.isdigit():
            target_id = int(raw)
    row = _resolve_target(ctx, target_id if target_id is not None else None)
    if row is None:
        await update.message.reply_text(
            "⚠️ Không tìm thấy lịch. Gõ `/sync <id>` hoặc /list.",
            parse_mode=ParseMode.MARKDOWN,
        )
        audit(req, "/sync", params=str(target_id), result="fail",
              error_message="row not found")
        return
    # Phase 3 — permission check
    ok, reason = can_modify_event(req, row)
    if not ok:
        await update.message.reply_text(reason, parse_mode=ParseMode.MARKDOWN)
        audit(req, "/sync", params=f"id={row.id}", result="reject",
              error_message=reason)
        return
    await update.message.reply_text("⏳ Đang so sánh với Calendar…")
    try:
        diffs = compute_drift(row)
    except Exception as e:
        log.exception("compute_drift failed")
        await update.message.reply_text(
            f"❌ Lỗi khi so sánh: `{e}`", parse_mode=ParseMode.MARKDOWN,
        )
        return
    if not diffs:
        await update.message.reply_text(
            f"✅ Lịch *{row.topic}* (id={row.id}) đã đồng bộ.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    ctx.chat_data["pending_sync"] = {"event_id": row.id, "diffs": diffs}
    await update.message.reply_text(
        f"{format_drift(diffs)}\n\n"
        f"Bot sẽ coi Calendar là nguồn đúng → update Zoom + DB theo Calendar.\n"
        f"Chị confirm?",
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Sync ngay", callback_data=f"sync_go:{row.id}"),
            InlineKeyboardButton("❌ Huỷ", callback_data="sync_no"),
        ]]),
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Quick-edit (Phương án A hybrid) ────────────────────────────────────────────
async def _handle_quick_edit(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    field: str,
    value: str,
    target: TargetSpec,
) -> None:
    """Resolve target event (id / natural spec / latest), then preview + confirm.

    Natural-spec branches:
      - exactly 1 match → proceed like an id target
      - 0 matches       → ask chị dùng /list hoặc #id
      - N>1 matches     → show disambiguation list, chị bấm số để chọn
    """
    candidates = _resolve_targets(ctx, target)
    if not candidates:
        # Phase 3 — group hint /mylist (member không dùng được /list)
        mode = ctx.chat_data.get("request_mode", "personal")
        list_cmd = "/mylist" if mode == "group" else "/list"
        await update.message.reply_text(
            "⚠️ Không tìm thấy lịch khớp. "
            "Thử `#id` (VD `sửa giờ 15h #3`), thêm `\"tên lịch\"` hoặc `ngày DD/MM`, "
            f"hoặc gõ {list_cmd} để xem lại."
        )
        return
    if len(candidates) > 1:
        await _show_quick_edit_disambig(update, ctx, field, value, candidates)
        return

    await _apply_quick_edit_to_row(update, ctx, candidates[0], field, value)


async def _apply_quick_edit_to_row(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    row: db.EventRow,
    field: str,
    value: str,
) -> None:
    """Preview + confirm flow once the target row is known.

    Used by _handle_quick_edit (1-match natural path) and the disambig callback."""
    reply = update.effective_message

    if field == "delete":
        ctx.chat_data["pending_delete"] = {"event_id": row.id}
        detail = formatter.format_event_detail(row)
        note = ""
        if row.recurring:
            note = (
                "\n\n⚠️ Đây là lịch lặp. Xoá tại đây sẽ huỷ *toàn bộ series*. "
                f"Muốn huỷ 1 buổi thôi thì {_list_cmd_for(ctx.chat_data)} → bấm lịch → 🗑 → *Chỉ 1 buổi*."
            )
        await reply.reply_text(
            f"🗑 Xoá lịch sau?\n\n{detail}{note}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Xoá + gửi mail", callback_data=f"del_all:{row.id}:n"),
                 InlineKeyboardButton("✅ Xoá (không mail)", callback_data=f"del_all:{row.id}:s")],
                [InlineKeyboardButton("❌ Huỷ", callback_data="del_no")],
            ]),
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
        return

    try:
        new_value, display = _parse_edit(row, field, value)
    except ParseError as e:
        await reply.reply_text(f"⚠️ {e}")
        return

    ctx.chat_data["pending_edit"] = {
        "event_id": row.id,
        "field": field,
        "new_value": new_value,
        "display": display,
    }
    preview = formatter.format_edit_preview(row, field, display)
    if field == "time":
        preview += formatter.format_conflict_warning(
            _conflicts_for_time_edit(row, new_value)
        )
    await reply.reply_text(
        preview,
        reply_markup=_edit_confirm_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


def _conflicts_for_time_edit(
    row: db.EventRow, new_start_iso: str
) -> list[tuple[db.EventRow, str]]:
    """Check the new start time against every other active event, excluding self."""
    return db.find_conflicts(new_start_iso, row.duration_min, exclude_id=row.id)


def _resolve_target(
    ctx: ContextTypes.DEFAULT_TYPE, target: int | TargetSpec | None
) -> db.EventRow | None:
    """Single-row resolver. For natural specs with many matches returns the *first* —
    callers that need disambiguation use `_resolve_targets` instead."""
    if isinstance(target, int):
        target = TargetSpec(id=target)
    rows = _resolve_targets(ctx, target)
    return rows[0] if rows else None


def _resolve_targets(
    ctx: ContextTypes.DEFAULT_TYPE, target: TargetSpec | None
) -> list[db.EventRow]:
    """Return every active event matching `target`.

    - target None / empty → latest in this chat, fallback latest DB row.
    - target.id           → exact lookup.
    - target natural      → search_events with filters (topic/attendee/date).
    """
    if target is None or target.is_empty:
        last_id = ctx.chat_data.get("last_created_id")
        if last_id:
            row = db.get_event(last_id)
            if row and row.status == "active":
                return [row]
        row = db.latest_created()
        return [row] if row else []

    if target.id is not None:
        row = db.get_event(target.id)
        if row and row.status == "active":
            return [row]
        return []

    return db.search_events(
        topic_contains=target.topic,
        attendee_contains=target.attendee,
        date_from=target.date,
        date_to=target.date,
        limit=20,
    )


async def _show_quick_edit_disambig(
    update: Update,
    ctx: ContextTypes.DEFAULT_TYPE,
    field: str,
    value: str,
    candidates: list[db.EventRow],
) -> None:
    """When a natural-target quick-edit matches >1 lịch, let chị pick one."""
    ctx.chat_data["pending_quick_disambig"] = {
        "field": field, "value": value,
        "candidate_ids": [r.id for r in candidates],
    }
    action_label = {
        "delete": "xoá", "time": "đổi giờ/ngày", "dur": "đổi thời lượng",
        "topic": "đổi tên", "ag": "đổi nội dung",
        "att_add": "thêm khách", "att_rm": "bỏ khách",
    }.get(field, "sửa")
    text = formatter.format_candidate_list(candidates, action_label)
    buttons = [
        InlineKeyboardButton(str(i), callback_data=f"qd_sel:{r.id}")
        for i, r in enumerate(candidates, 1)
    ]
    rows_kb = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    rows_kb.append([InlineKeyboardButton("❌ Huỷ", callback_data="qd_cancel")])
    await update.message.reply_text(
        text, reply_markup=InlineKeyboardMarkup(rows_kb),
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Occurrence helpers (recurring series — single-buổi ops) ────────────────────
def _fetch_occurrences(row: db.EventRow) -> list[dict]:
    """Join Zoom occurrences + Calendar instances, sorted by start time.

    Returns list of dicts:
      {idx, start_local, zoom_occ_id, cal_instance_id, cancelled}
    """
    if not row.recurring:
        return []
    is_personal = row.provider == "meet"
    if is_personal:
        zoom_occs: list = []
    else:
        zoom_detail = _zoom.get_meeting(row.zoom_meeting_id)
        zoom_occs = sorted(
            zoom_detail.get("occurrences", []),
            key=lambda o: o["start_time"],
        )
    cal_instances = _get_calendar().list_instances(
        row.calendar_event_id, calendar_id=_calendar_id_for_row(row),
    )
    cal_instances_sorted = sorted(
        cal_instances,
        key=lambda e: (e.get("originalStartTime", {}).get("dateTime")
                       or e.get("start", {}).get("dateTime") or ""),
    )

    # Compute deterministic local start times from recurrence
    start = row.start_dt
    expected = [start + timedelta(weeks=i) for i in range(row.recurring["count"])]

    out: list[dict] = []
    for i, local_start in enumerate(expected):
        zoom_occ_id = (
            str(zoom_occs[i]["occurrence_id"]) if i < len(zoom_occs) else ""
        )
        cal_id = cal_instances_sorted[i]["id"] if i < len(cal_instances_sorted) else ""
        cal_status = (
            cal_instances_sorted[i].get("status", "")
            if i < len(cal_instances_sorted)
            else ""
        )
        is_cancelled = (
            cal_status == "cancelled"
            or local_start.isoformat(timespec="seconds") in row.cancelled_occurrences
        )
        out.append({
            "idx": i,
            "start_local": local_start.isoformat(timespec="seconds"),
            "zoom_occ_id": zoom_occ_id,
            "cal_instance_id": cal_id,
            "cancelled": is_cancelled,
        })
    return out


def _occurrence_keyboard(
    event_id: int, occurrences: list[dict], prefix: str
) -> InlineKeyboardMarkup:
    """Buttons numbered 1..N for each active occurrence."""
    buttons = [
        InlineKeyboardButton(str(i + 1), callback_data=f"{prefix}:{event_id}:{i}")
        for i, occ in enumerate(occurrences)
        if not occ["cancelled"]
    ]
    rows = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    rows.append([InlineKeyboardButton("↩️ Quay lại", callback_data=f"back_det:{event_id}")])
    return InlineKeyboardMarkup(rows)


# ── Callback router ────────────────────────────────────────────────────────────
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    # Phase 3: gate qua resolve_context (chat_mode aware) thay vì _is_allowed
    # cũ (chỉ check ALLOWED_CHAT_ID = personal). Bug: mọi click button trong
    # group bị silent-drop trước khi fix này.
    _log_incoming(update)
    perm_ctx = resolve_context(update)
    if perm_ctx.mode == "reject":
        # Silent — không pollute chat lạ. audit để chị Yến /audit thấy probe.
        audit(perm_ctx, "callback", result="reject",
              error_message=perm_ctx.reject_message)
        return
    query = update.callback_query
    await query.answer()
    data = query.data or ""

    try:
        if data == "cr_confirm":
            await _do_create(update, ctx)
            return
        if data == "cr_cancel":
            ctx.chat_data.pop("pending", None)
            ctx.chat_data.pop("dir_mode", None)
            await query.edit_message_text("❌ Đã huỷ tạo lịch. Gửi lệnh mới khi cần nhé.")
            return

        # ── Directory picker (Section 14) ────────────────────────────────────
        if data == "dir_noop":
            return

        if data.startswith("dir_open:"):
            parts = data.split(":")
            kind_raw = parts[1] if len(parts) > 1 else ""
            event_id: int | None = None
            if kind_raw == "create":
                kind = "create"
            elif kind_raw == "edit" and len(parts) > 2:
                kind = "edit_add"
                try:
                    event_id = int(parts[2])
                except ValueError:
                    await query.edit_message_text("⚠️ Callback hỏng.")
                    return
            elif kind_raw == "ext":
                kind = "ext_add"
                # ext_add cần có current_ext
                if not ctx.chat_data.get("current_ext"):
                    await query.edit_message_text("⚠️ Không còn dữ liệu lịch Calendar.")
                    return
            else:
                await query.edit_message_text("⚠️ Callback hỏng.")
                return
            _enter_dir_mode(ctx, kind=kind, event_id=event_id)
            await _render_directory_panel(query, ctx)
            return

        if data.startswith("dir_p:"):
            try:
                page = int(data.split(":", 1)[1])
            except ValueError:
                return
            dm = ctx.chat_data.get("dir_mode")
            if not dm:
                await query.edit_message_text("⚠️ Phiên chọn sổ đã hết hạn.")
                return
            dm["page"] = max(1, page)
            await _render_directory_panel(query, ctx)
            return

        if data == "dir_all":
            dm = ctx.chat_data.get("dir_mode")
            if not dm:
                await query.edit_message_text("⚠️ Phiên chọn sổ đã hết hạn.")
                return
            # Tick TOÀN BỘ thành viên trong sổ. Base attendees giữ nguyên ✓.
            base_emails = {e.lower() for e in _base_attendees_for_dir_mode(ctx)}
            all_emails = [m.email for m in directory.list_members()]
            sel = list(dm.get("selected_emails") or [])
            sel_set = set(sel)
            for e in all_emails:
                if e in base_emails:
                    # Vẫn lưu để hiển thị ✓; không thêm trùng
                    if e not in sel_set:
                        sel.append(e)
                        sel_set.add(e)
                else:
                    if e not in sel_set:
                        sel.append(e)
                        sel_set.add(e)
            dm["selected_emails"] = sel
            await _render_directory_panel(query, ctx)
            return

        if data == "dir_clear":
            dm = ctx.chat_data.get("dir_mode")
            if not dm:
                await query.edit_message_text("⚠️ Phiên chọn sổ đã hết hạn.")
                return
            # Reset về base — chỉ giữ lại những người vốn đã trong lịch (✓)
            base_emails = [e.lower() for e in _base_attendees_for_dir_mode(ctx)]
            dm["selected_emails"] = base_emails
            await _render_directory_panel(query, ctx)
            return

        if data.startswith("dir_t:"):
            try:
                idx = int(data.split(":", 1)[1])
            except ValueError:
                return
            dm = ctx.chat_data.get("dir_mode")
            if not dm:
                await query.edit_message_text("⚠️ Phiên chọn sổ đã hết hạn.")
                return
            m = directory.member_at_index(idx)
            if m is None:
                # Sổ thay đổi giữa session — re-render với danh sách mới
                await _render_directory_panel(query, ctx)
                return
            email = m.email
            sel = list(dm.get("selected_emails") or [])
            base_emails = {e.lower() for e in _base_attendees_for_dir_mode(ctx)}
            if email in base_emails:
                # Người này đã có trong lịch — không cho toggle off (picker chỉ ADD)
                # Nhưng vẫn re-render để tránh dead-feel
                await _render_directory_panel(query, ctx)
                return
            if email in sel:
                sel = [e for e in sel if e != email]
            else:
                sel.append(email)
            dm["selected_emails"] = sel
            await _render_directory_panel(query, ctx)
            return

        if data == "dir_done":
            dm = ctx.chat_data.get("dir_mode")
            if not dm:
                await query.edit_message_text("⚠️ Phiên chọn sổ đã hết hạn.")
                return
            kind = dm.get("kind")
            if kind == "create":
                await _exit_dir_mode_back_to_create(query, ctx, apply=True)
            elif kind == "edit_add":
                await _exit_dir_mode_to_edit_add(query, ctx, apply=True)
            elif kind == "ext_add":
                await _exit_dir_mode_to_ext_add(query, ctx, apply=True)
            else:
                ctx.chat_data.pop("dir_mode", None)
                await query.edit_message_text("❌ Phiên chọn sổ đã đóng.")
            return

        if data == "dir_cancel":
            dm = ctx.chat_data.get("dir_mode") or {}
            kind = dm.get("kind")
            if kind == "create":
                await _exit_dir_mode_back_to_create(query, ctx, apply=False)
            elif kind == "edit_add":
                await _exit_dir_mode_to_edit_add(query, ctx, apply=False)
            elif kind == "ext_add":
                await _exit_dir_mode_to_ext_add(query, ctx, apply=False)
            else:
                ctx.chat_data.pop("dir_mode", None)
                await query.edit_message_text("❌ Đã đóng sổ.")
            return

        # ── Review picker (Phase 14) — untick khách trực tiếp trong preview ──
        if data.startswith("rev_open:"):
            kind = data.split(":", 1)[1]
            if kind != "create":
                await query.edit_message_text("⚠️ Review picker chỉ hỗ trợ flow tạo lịch hiện tại.")
                return
            cmd = ctx.chat_data.get("pending")
            if cmd is None:
                await query.edit_message_text("⚠️ Phiên tạo lịch đã hết hạn.")
                return
            await query.edit_message_text(
                formatter.format_review_panel(attendees=list(cmd.attendees or [])),
                reply_markup=_review_picker_keyboard(list(cmd.attendees or [])),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if data.startswith("rev_rm:"):
            try:
                idx = int(data.split(":", 1)[1])
            except ValueError:
                return
            cmd = ctx.chat_data.get("pending")
            if cmd is None:
                await query.edit_message_text("⚠️ Phiên tạo lịch đã hết hạn.")
                return
            attendees = list(cmd.attendees or [])
            if 0 <= idx < len(attendees):
                attendees.pop(idx)
                cmd.attendees = attendees
                ctx.chat_data["pending"] = cmd
            await query.edit_message_text(
                formatter.format_review_panel(attendees=attendees),
                reply_markup=_review_picker_keyboard(attendees),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if data == "rev_clear":
            cmd = ctx.chat_data.get("pending")
            if cmd is None:
                await query.edit_message_text("⚠️ Phiên tạo lịch đã hết hạn.")
                return
            cmd.attendees = []
            ctx.chat_data["pending"] = cmd
            await query.edit_message_text(
                formatter.format_review_panel(attendees=[]),
                reply_markup=_review_picker_keyboard([]),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if data == "rev_back":
            cmd = ctx.chat_data.get("pending")
            if cmd is None:
                await query.edit_message_text("⚠️ Phiên tạo lịch đã hết hạn.")
                return
            conflicts = _collect_conflicts_for(cmd)
            preview = (
                formatter.format_confirm_preview(cmd)
                + formatter.format_conflict_warning(conflicts)
            )
            await query.edit_message_text(
                preview,
                reply_markup=_preview_keyboard_for(cmd),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if data == "back_list":
            # Prefer restoring paged/filtered list if one is active, else legacy view
            mode = ctx.chat_data.get("request_mode")
            owner = ctx.chat_data.get("request_user_id") if ctx.chat_data.get("mylist_only") else None
            if ctx.chat_data.get("list_query"):
                await _render_list_query(
                    query, ctx, _query_from_chat_data(ctx),
                    chat_mode=mode, created_by_user_id=owner,
                )
                return
            rows = db.list_recent(limit=10, chat_mode=mode, created_by_user_id=owner)
            await query.edit_message_text(
                formatter.format_list(rows),
                reply_markup=_list_keyboard(rows),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if data == "lp_noop":
            return

        if data.startswith("lp:"):
            page = int(data.split(":", 1)[1])
            q = _query_from_chat_data(ctx)
            q.page = max(1, page)
            mode = ctx.chat_data.get("request_mode")
            owner = ctx.chat_data.get("request_user_id") if ctx.chat_data.get("mylist_only") else None
            await _render_list_query(query, ctx, q, chat_mode=mode, created_by_user_id=owner)
            return

        if data.startswith("qd_sel:"):
            chosen_id = int(data.split(":", 1)[1])
            pending = ctx.chat_data.pop("pending_quick_disambig", None)
            if not pending:
                await query.edit_message_text("⚠️ Phiên chọn đã hết hạn.")
                return
            field = pending["field"]
            value = pending["value"]
            row = db.get_event(chosen_id)
            if not row or row.status != "active":
                await query.edit_message_text("⚠️ Lịch đã không còn.")
                return
            # Collapse disambig view + re-run quick-edit flow on chosen row
            await query.edit_message_text(
                f"✅ Đã chọn lịch id={chosen_id}: *{row.topic}* — đang xử lý…",
                parse_mode=ParseMode.MARKDOWN,
            )
            await _apply_quick_edit_to_row(update, ctx, row, field, value)
            return

        if data == "qd_cancel":
            ctx.chat_data.pop("pending_quick_disambig", None)
            await query.edit_message_text("❌ Đã huỷ lệnh sửa.")
            return

        if data.startswith("clone_sel:"):
            chosen_id = int(data.split(":", 1)[1])
            pending = ctx.chat_data.pop("pending_clone_disambig", None)
            if not pending:
                await query.edit_message_text("⚠️ Phiên chọn đã hết hạn.")
                return
            source = db.get_event(chosen_id)
            if not source or source.status != "active":
                await query.edit_message_text("⚠️ Lịch nguồn không còn.")
                return
            overrides = _dict_to_overrides(pending.get("overrides", {}))
            await _preview_clone(query, ctx, source, overrides)
            return

        if data == "clone_cancel":
            ctx.chat_data.pop("pending_clone_disambig", None)
            await query.edit_message_text("❌ Đã huỷ lệnh clone.")
            return

        # ── External (Calendar) edit/delete flow ─────────────────────────────
        if data.startswith("ext_sel:"):
            idx = int(data.split(":", 1)[1])
            await _show_ext_detail(query, ctx, idx)
            return

        if data == "ext_back":
            await _show_ext_detail(query, ctx)
            return

        if data == "ext_ed_menu":
            occ = ctx.chat_data.get("current_ext")
            if not occ:
                await query.edit_message_text("⚠️ Không còn dữ liệu lịch.")
                return
            await query.edit_message_text(
                f"✏️ Sửa lịch Calendar *{occ['topic']}*\nChọn field muốn sửa:",
                reply_markup=_ext_edit_menu_keyboard(),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if data.startswith("ext_ed_f:"):
            field = data.split(":", 1)[1]
            if not ctx.chat_data.get("current_ext"):
                await query.edit_message_text("⚠️ Không còn dữ liệu lịch.")
                return
            ctx.chat_data["ext_edit_mode"] = {"field": field}
            extra_kb = (
                _att_add_prompt_keyboard(kind="ext")
                if field == "att_add" else None
            )
            await query.edit_message_text(
                f"✏️ *{formatter.edit_prompt(field)}*\n_(lịch từ Calendar)_",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=extra_kb,
            )
            return

        if data.startswith("ext_ed_go:"):
            notify = data.endswith(":n")
            await _do_ext_edit(query, ctx, notify=notify)
            return

        if data == "ext_ed_no":
            ctx.chat_data.pop("pending_ext_edit", None)
            await _show_ext_detail(query, ctx, prefix="❌ Đã huỷ sửa.\n\n")
            return

        if data == "ext_del_ask":
            occ = ctx.chat_data.get("current_ext")
            if not occ:
                await query.edit_message_text("⚠️ Không còn dữ liệu lịch.")
                return
            recur_tag = (
                " _(đây là 1 buổi của lịch lặp — xoá chỉ ảnh hưởng buổi này)_"
                if occ.get("recurring_source_id") else ""
            )
            await query.edit_message_text(
                f"🗑 Xoá lịch Calendar *{occ['topic']}*?{recur_tag}\n"
                f"Khách sẽ nhận email huỷ.",
                reply_markup=_ext_delete_confirm_keyboard(),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if data.startswith("ext_del_yes:"):
            notify = data.endswith(":n")
            await _do_ext_delete(query, ctx, notify=notify)
            return

        if data.startswith("ls_sel:") or data.startswith("back_det:"):
            event_id = int(data.split(":", 1)[1])
            await _show_detail(query, event_id)
            return

        if data.startswith("ed_menu:"):
            event_id = int(data.split(":", 1)[1])
            row = db.get_event(event_id)
            if not row:
                await query.edit_message_text("⚠️ Lịch không tồn tại.")
                return
            await query.edit_message_text(
                f"✏️ Sửa lịch id={event_id}: *{row.topic}*\nChọn field muốn sửa:",
                reply_markup=_edit_menu_keyboard(event_id, recurring=bool(row.recurring)),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if data.startswith("ed_f:"):
            _, eid, field = data.split(":", 2)
            event_id = int(eid)
            ctx.chat_data["edit_mode"] = {"event_id": event_id, "field": field}
            extra_kb = (
                _att_add_prompt_keyboard(kind="edit", event_id=event_id)
                if field == "att_add" else None
            )
            await query.edit_message_text(
                f"✏️ *{formatter.edit_prompt(field)}*\n(id={event_id})",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=extra_kb,
            )
            return

        # Recurring — chọn 1 buổi để SỬA
        if data.startswith("ed_occ:"):
            event_id = int(data.split(":", 1)[1])
            await _show_occurrence_picker(query, ctx, event_id, action="sửa")
            return

        if data.startswith("ed_occ_sel:"):
            _, eid, idx = data.split(":", 2)
            event_id, i = int(eid), int(idx)
            occs = ctx.chat_data.get("occurrences", {}).get(event_id)
            if not occs or i >= len(occs):
                await query.edit_message_text(
                    f"⚠️ Danh sách đã cũ. Gõ {_list_cmd_for(ctx.chat_data)} làm lại."
                )
                return
            await query.edit_message_text(
                f"✏️ Sửa buổi {formatter.format_occurrence_date(occs[i])} "
                f"(lịch id={event_id}). Chọn field:",
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🕐 Giờ/ngày",
                                             callback_data=f"ed_occ_f:{event_id}:{i}:time"),
                        InlineKeyboardButton("⏱ Thời lượng",
                                             callback_data=f"ed_occ_f:{event_id}:{i}:dur"),
                    ],
                    [InlineKeyboardButton("↩️ Quay lại",
                                          callback_data=f"ed_occ:{event_id}")],
                ]),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if data.startswith("ed_occ_f:"):
            _, eid, idx, field = data.split(":", 3)
            event_id, i = int(eid), int(idx)
            ctx.chat_data["edit_mode"] = {
                "event_id": event_id, "field": field, "occurrence_idx": i,
            }
            await query.edit_message_text(
                f"✏️ *{formatter.edit_prompt(field)}*\n"
                f"(chỉ áp dụng cho buổi đã chọn, id={event_id})",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if data.startswith("ed_go:"):
            notify = data.endswith(":n")
            await _do_edit(update, ctx, notify=notify)
            return
        if data == "ed_no":
            pending = ctx.chat_data.pop("pending_edit", None)
            eid = pending.get("event_id") if pending else None
            if eid:
                await _show_detail(query, eid, prefix="❌ Đã huỷ sửa.\n\n")
            else:
                await query.edit_message_text("❌ Đã huỷ.")
            return

        if data.startswith("del_ask:"):
            event_id = int(data.split(":", 1)[1])
            row = db.get_event(event_id)
            if not row:
                await query.edit_message_text("⚠️ Lịch không tồn tại.")
                return
            if row.recurring:
                await query.edit_message_text(
                    f"🗑 Xoá lịch lặp *{row.topic}* (id={event_id}, "
                    f"{row.recurring['count']} buổi)?",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("❌ Toàn bộ + gửi mail",
                                              callback_data=f"del_all:{event_id}:n")],
                        [InlineKeyboardButton("❌ Toàn bộ (không mail)",
                                              callback_data=f"del_all:{event_id}:s")],
                        [InlineKeyboardButton("⊘ Chỉ 1 buổi",
                                              callback_data=f"del_one:{event_id}")],
                        [InlineKeyboardButton("↩️ Quay lại",
                                              callback_data=f"back_det:{event_id}")],
                    ]),
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                await query.edit_message_text(
                    f"🗑 Xoá lịch *{row.topic}* (id={event_id})?\n"
                    f"Khách sẽ nhận email huỷ.",
                    reply_markup=_delete_confirm_keyboard(event_id),
                    parse_mode=ParseMode.MARKDOWN,
                )
            return

        if data.startswith("del_yes:") or data.startswith("del_all:"):
            parts = data.split(":")
            event_id = int(parts[1])
            notify = len(parts) < 3 or parts[2] == "n"
            ctx.chat_data.pop("pending_delete", None)
            await _do_delete(query, event_id, notify=notify, update=update)
            return

        if data == "del_no":
            ctx.chat_data.pop("pending_delete", None)
            await query.edit_message_text("❌ Đã huỷ lệnh xoá.")
            return

        if data.startswith("sync_go:"):
            event_id = int(data.split(":", 1)[1])
            pending = ctx.chat_data.pop("pending_sync", None)
            row = db.get_event(event_id)
            if not row or not pending or pending.get("event_id") != event_id:
                await query.edit_message_text("⚠️ Phiên sync đã hết hạn.")
                return
            # Phase 3 — permission check
            sync_perm = resolve_context(update)
            ok, reason = can_modify_event(sync_perm, row)
            if not ok:
                await query.edit_message_text(reason, parse_mode=ParseMode.MARKDOWN)
                audit(sync_perm, "/sync", params=f"id={event_id}",
                      result="reject", error_message=reason)
                return
            await query.edit_message_text("⏳ Đang sync Zoom + DB theo Calendar…")
            try:
                _apply_drift_sync(row, pending["diffs"])
                updated = db.get_event(event_id)
                # Phase 3 — group mode: gộp actor + diff Calendar→DB vào
                # reply (skip notify_sync riêng để tránh duplicate).
                actor_block = ""
                row_mode = (updated or row).chat_mode or "personal"
                diffs = pending.get("diffs", {})
                notify_keys = {"start", "duration", "topic", "attendees"}
                if row_mode == "group" and (set(diffs) & notify_keys):
                    actor_cfg = get_user(sync_perm.user_id)
                    team_str = f" ({actor_cfg.team})" if actor_cfg else ""
                    diff_lines = []
                    if "start" in diffs:
                        diff_lines.append(
                            f"  • Giờ: {diffs['start'][0]} → {diffs['start'][1]}"
                        )
                    if "duration" in diffs:
                        diff_lines.append(
                            f"  • Thời lượng: {diffs['duration'][0]}p → {diffs['duration'][1]}p"
                        )
                    if "topic" in diffs:
                        diff_lines.append(
                            f"  • Tên: {diffs['topic'][0]} → {diffs['topic'][1]}"
                        )
                    if "attendees" in diffs:
                        old_a = diffs['attendees'][0]
                        new_a = diffs['attendees'][1]
                        diff_lines.append(
                            f"  • Khách: {len(old_a)} → {len(new_a)} người"
                        )
                    actor_block = (
                        f"👤 *Sửa bởi:* {sync_perm.display_name}{team_str}\n"
                        f"🔄 Thay đổi (Calendar → Bot + Zoom):\n"
                        + "\n".join(diff_lines) + "\n"
                    )
                await query.edit_message_text(
                    f"✅ Đã đồng bộ.\n{actor_block}\n"
                    + formatter.format_event_detail(updated),
                    reply_markup=_detail_keyboard(event_id),
                    parse_mode=ParseMode.MARKDOWN,
                    disable_web_page_preview=True,
                )
            except Exception as e:
                log.exception("Sync apply failed")
                await query.edit_message_text(
                    f"❌ Lỗi sync: `{e}`", parse_mode=ParseMode.MARKDOWN
                )
            return
        if data == "sync_no":
            ctx.chat_data.pop("pending_sync", None)
            await query.edit_message_text("❌ Đã huỷ sync.")
            return

        if data.startswith("sync_ask:"):
            event_id = int(data.split(":", 1)[1])
            row = db.get_event(event_id)
            if not row:
                await query.edit_message_text("⚠️ Lịch không tồn tại.")
                return
            await query.edit_message_text("⏳ Đang kiểm tra drift…")
            try:
                diffs = compute_drift(row)
            except Exception as e:
                await query.edit_message_text(
                    f"❌ Lỗi so sánh: `{e}`", parse_mode=ParseMode.MARKDOWN
                )
                return
            if not diffs:
                await query.edit_message_text(
                    "✅ Không còn lệch. Lịch đã đồng bộ.",
                    reply_markup=_detail_keyboard(event_id),
                )
                return
            ctx.chat_data["pending_sync"] = {"event_id": event_id, "diffs": diffs}
            await query.edit_message_text(
                f"{format_drift(diffs)}\n\n"
                f"Coi Calendar là nguồn đúng → update Zoom + DB theo Calendar?",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Sync ngay",
                                         callback_data=f"sync_go:{event_id}"),
                    InlineKeyboardButton("❌ Huỷ",
                                         callback_data=f"back_det:{event_id}"),
                ]]),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        # Recurring — chọn 1 buổi để XOÁ
        if data.startswith("del_one:"):
            event_id = int(data.split(":", 1)[1])
            await _show_occurrence_picker(query, ctx, event_id, action="xoá")
            return

        if data.startswith("del_occ_ask:"):
            _, eid, idx = data.split(":", 2)
            event_id, i = int(eid), int(idx)
            occs = ctx.chat_data.get("occurrences", {}).get(event_id)
            if not occs or i >= len(occs):
                await query.edit_message_text(
                    f"⚠️ Danh sách đã cũ. Gõ {_list_cmd_for(ctx.chat_data)} làm lại."
                )
                return
            row = db.get_event(event_id)
            if not row:
                await query.edit_message_text("⚠️ Lịch không tồn tại.")
                return
            date_str = formatter.format_occurrence_date(occs[i])
            await query.edit_message_text(
                f"🗑 Xoá buổi *{date_str}* trong lịch lặp *{row.topic}*?",
                reply_markup=_delete_occ_confirm_keyboard(event_id, i),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        if data.startswith("del_occ_yes:"):
            parts = data.split(":")
            event_id, i = int(parts[1]), int(parts[2])
            notify = parts[3] == "n" if len(parts) > 3 else True
            occs = ctx.chat_data.get("occurrences", {}).get(event_id)
            if not occs or i >= len(occs):
                await query.edit_message_text(
                    f"⚠️ Danh sách đã cũ. Gõ {_list_cmd_for(ctx.chat_data)} làm lại."
                )
                return
            await _do_delete_occurrence(query, event_id, occs[i], notify=notify)
            return

        log.warning("Unknown callback: %s", data)
    except Exception as e:
        log.exception("Callback handler failed")
        await query.edit_message_text(f"❌ Lỗi: `{e}`", parse_mode=ParseMode.MARKDOWN)


# ── Action: create ─────────────────────────────────────────────────────────────
async def _do_create(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    cmd = ctx.chat_data.get("pending")
    if cmd is None:
        await query.edit_message_text("⚠️ Phiên confirm đã hết hạn. Gửi lại lệnh giúp em.")
        return
    # Phase 3 — chat_mode + creator info từ ctx.chat_data (set bởi handle_text)
    req_mode = ctx.chat_data.get("request_mode", "personal")
    req_user_id = ctx.chat_data.get("request_user_id", 8173041182)
    req_display = ctx.chat_data.get("request_display_name", "Hải Yến")
    creator_cfg: UserConfig | None = get_user(req_user_id)
    try:
        if cmd.is_personal:
            await _do_create_personal(query, ctx, cmd)
            return
        await query.edit_message_text("⏳ Đang tạo Zoom + Calendar event…")
        recurrence = None
        if cmd.recurring:
            recurrence = build_weekly_recurrence(
                rrule_byday=cmd.recurring["byday"],
                count=cmd.recurring["count"],
            )
        start_iso = cmd.start.strftime("%Y-%m-%dT%H:%M:%S")
        zoom = _zoom.create_meeting(
            topic=cmd.topic,
            start_local_iso=start_iso,
            duration_min=cmd.duration_min,
            agenda=cmd.agenda,
            recurrence=recurrence,
        )
        end_local_iso = (cmd.start + timedelta(minutes=cmd.duration_min)).strftime(
            "%Y-%m-%dT%H:%M:%S"
        )
        rrule = None
        if cmd.recurring:
            rrule = f"RRULE:FREQ=WEEKLY;BYDAY={cmd.recurring['byday']};COUNT={cmd.recurring['count']}"

        # Phase 3 — branch theo mode để chọn template + calendar_id + color
        if req_mode == "group" and creator_cfg is not None:
            from bot.permissions import CALENDAR_TEAM_ID
            target_cal = CALENDAR_TEAM_ID() or None
            # Phase 3.x: chỉ enforce color cho member (Hương/Thuỳ) để phân biệt
            # creator visually trên Calendar UI. Admin (Yến) dùng default
            # Calendar TEAM giống personal mode (chị tự setup màu Calendar UI).
            target_color = (
                creator_cfg.calendar_color
                if creator_cfg.role == "member" else None
            )
            title_prefix = creator_cfg.title_prefix
            # Group attendees = khách + creator email (Kịch bản B)
            final_attendees = list(cmd.attendees)
            if creator_cfg.email and creator_cfg.email not in final_attendees:
                final_attendees.append(creator_cfg.email)
            description = formatter.format_group_calendar_description(
                cmd=cmd,
                zoom_join_url=zoom.join_url,
                zoom_meeting_id=zoom.meeting_id,
                zoom_passcode=zoom.passcode,
                creator_display_name=creator_cfg.display_name,
                creator_team=creator_cfg.team,
                creator_signature=creator_cfg.signature,
            )
        else:
            # Personal mode: giữ behavior cũ — KHÔNG enforce colorId, để
            # Calendar dùng màu default của calendar (chị Yến đã set sẵn
            # màu cam trên Calendar primary). Group mode mới override color
            # theo USERS để phân biệt người tạo.
            target_cal = None  # primary
            target_color = None  # default calendar color
            title_prefix = (creator_cfg.title_prefix if creator_cfg
                            else "[John Academy] ")
            final_attendees = list(cmd.attendees)
            description = formatter.format_calendar_description(
                cmd=cmd,
                zoom_join_url=zoom.join_url,
                zoom_meeting_id=zoom.meeting_id,
                zoom_passcode=zoom.passcode,
            )

        event = _get_calendar().create_event(
            summary=f"{title_prefix}{cmd.topic}",
            description=description,
            start_local_iso=start_iso,
            end_local_iso=end_local_iso,
            attendee_emails=final_attendees,
            rrule=rrule,
            calendar_id=target_cal,
            color_id=target_color,
        )
        event_id = db.insert_event(
            topic=cmd.topic,
            start_local=start_iso,
            duration_min=cmd.duration_min,
            agenda=cmd.agenda,
            attendees=final_attendees,
            recurring=cmd.recurring,
            zoom_meeting_id=str(zoom.meeting_id),
            zoom_join_url=zoom.join_url,
            zoom_passcode=zoom.passcode,
            calendar_event_id=event.event_id,
            calendar_event_link=event.html_link,
            created_by_user_id=req_user_id,
            created_by_display_name=req_display,
            chat_mode=req_mode,
        )
        ctx.chat_data["last_created_id"] = event_id
        reply = formatter.format_success_reply(
            cmd=cmd,
            zoom_join_url=zoom.join_url,
            zoom_meeting_id=zoom.meeting_id,
            zoom_passcode=zoom.passcode,
            calendar_event_link=event.html_link,
        )
        # Phase 3 — group mode: gộp creator info vào reply (thay cho
        # notify_create riêng → tránh duplicate trong group).
        if req_mode == "group":
            team_str = f" ({creator_cfg.team})" if creator_cfg else ""
            creator_line = f"👤 *Người tạo:* {req_display}{team_str}\n"
            # Inject sau dòng đầu "✅ Đã tạo xong: *Title*"
            head, _, rest = reply.partition("\n")
            reply = f"{head}\n{creator_line}{rest}"
        # Phase 3 — group hint /mylist (vì /list admin only); personal /list
        list_cmd = "/mylist" if req_mode == "group" else "/list"
        await query.edit_message_text(
            reply + f"\n\n🆔 *DB id:* `{event_id}` — gõ {list_cmd} để sửa/xoá.",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.exception("Failed to create meeting")
        await query.edit_message_text(
            f"❌ Lỗi tạo lịch: `{e}`\nEm để lại log để debug.",
            parse_mode=ParseMode.MARKDOWN,
        )
    finally:
        ctx.chat_data.pop("pending", None)


async def _do_create_personal(query, ctx, cmd) -> None:
    """HY flow: Google Meet auto-link + visibility=private, no Zoom.

    Phase 3: HY chỉ chạy trong personal mode (handle_text đã guard).
    Attribution = chị Yến luôn.
    """
    req_user_id = ctx.chat_data.get("request_user_id", 8173041182)
    req_display = ctx.chat_data.get("request_display_name", "Hải Yến")
    await query.edit_message_text("⏳ Đang tạo lịch HY cá nhân (Meet, private)…")
    start_iso = cmd.start.strftime("%Y-%m-%dT%H:%M:%S")
    end_local_iso = (cmd.start + timedelta(minutes=cmd.duration_min)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    rrule = None
    if cmd.recurring:
        rrule = (
            f"RRULE:FREQ=WEEKLY;BYDAY={cmd.recurring['byday']};"
            f"COUNT={cmd.recurring['count']}"
        )
    # Step 1: create event with Meet auto-gen. Description placeholder first —
    # we don't know the Meet link yet.
    placeholder_desc = formatter.format_personal_calendar_description(
        cmd=cmd, meet_link=""
    )
    event = _get_calendar().create_event(
        summary=f"[HY] {cmd.topic}",
        description=placeholder_desc,
        start_local_iso=start_iso,
        end_local_iso=end_local_iso,
        attendee_emails=cmd.attendees,
        rrule=rrule,
        with_meet=True,
        visibility="private",
        notify=bool(cmd.attendees),
    )
    # Step 2: patch description now that we have the real Meet link
    meet_link = event.hangout_link or ""
    if meet_link:
        final_desc = formatter.format_personal_calendar_description(
            cmd=cmd, meet_link=meet_link
        )
        try:
            _get_calendar().patch_event(
                event.event_id,
                description=final_desc,
                notify=False,
            )
        except Exception:
            log.exception("Failed to patch HY description with Meet link (non-fatal)")
    event_id = db.insert_event(
        topic=cmd.topic,
        start_local=start_iso,
        duration_min=cmd.duration_min,
        agenda=cmd.agenda,
        attendees=cmd.attendees,
        recurring=cmd.recurring,
        zoom_meeting_id="",
        zoom_join_url="",
        zoom_passcode="",
        calendar_event_id=event.event_id,
        calendar_event_link=event.html_link,
        provider="meet",
        meet_join_url=meet_link,
        created_by_user_id=req_user_id,
        created_by_display_name=req_display,
        chat_mode="personal",
    )
    ctx.chat_data["last_created_id"] = event_id
    reply = formatter.format_personal_success_reply(
        cmd=cmd,
        meet_link=meet_link,
        calendar_event_link=event.html_link,
    )
    await query.edit_message_text(
        reply + f"\n\n🆔 *DB id:* `{event_id}` — gõ /list để sửa/xoá.",
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


# ── Action: edit ───────────────────────────────────────────────────────────────
async def _do_edit(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE, *, notify: bool = True
) -> None:
    query = update.callback_query
    pending = ctx.chat_data.get("pending_edit")
    if pending is None:
        await query.edit_message_text("⚠️ Phiên sửa đã hết hạn.")
        return
    event_id = pending["event_id"]
    field = pending["field"]
    new_value = pending["new_value"]

    row = db.get_event(event_id)
    # Phase 3 — permission check
    perm_ctx = resolve_context(update)
    if row is not None and row.status == "active":
        ok, reason = can_modify_event(perm_ctx, row)
        if not ok:
            ctx.chat_data.pop("pending_edit", None)
            await query.edit_message_text(reason, parse_mode=ParseMode.MARKDOWN)
            audit(perm_ctx, "edit_event", params=f"id={event_id} field={field}",
                  result="reject", error_message=reason)
            return
    if row is None or row.status != "active":
        ctx.chat_data.pop("pending_edit", None)
        await query.edit_message_text("⚠️ Lịch này không còn.")
        return

    occ_idx = pending.get("occurrence_idx")
    mail_note = "Khách sẽ nhận email cập nhật." if notify else "Không gửi email cho khách."
    await query.edit_message_text(f"⏳ Đang apply thay đổi lên Zoom + Calendar… ({mail_note})")
    try:
        if occ_idx is not None:
            occs = ctx.chat_data.get("occurrences", {}).get(event_id, [])
            if occ_idx >= len(occs):
                raise RuntimeError(f"Danh sách buổi đã cũ, gõ {_list_cmd_for(ctx.chat_data)} làm lại.")
            _apply_occurrence_edit(row, occs[occ_idx], field, new_value, notify=notify)
        else:
            _apply_edit(row, field, new_value, notify=notify)
        ctx.chat_data.pop("pending_edit", None)
        updated = db.get_event(event_id)
        # Phase 3 — group mode: gộp actor + diff vào reply (skip field='ag' =
        # nội dung agenda — Q1.A spec). Personal mode: reply như cũ.
        actor_diff_block = ""
        row_mode = (row.chat_mode or "personal") if row else "personal"
        if row_mode == "group" and field != "ag" and updated:
            actor_team = ""
            actor_cfg = get_user(perm_ctx.user_id) if perm_ctx else None
            if actor_cfg:
                actor_team = f" ({actor_cfg.team})"
            diff_str = _format_field_diff(row, updated, field)
            actor_diff_block = (
                f"👤 *Sửa bởi:* {perm_ctx.display_name}{actor_team}\n"
                f"{diff_str}\n"
            )
        await query.edit_message_text(
            f"✅ Đã cập nhật. {mail_note}\n{actor_diff_block}\n"
            + formatter.format_event_detail(updated),
            reply_markup=_detail_keyboard(event_id),
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.exception("Failed to apply edit")
        await query.edit_message_text(
            f"❌ Lỗi apply: `{e}`", parse_mode=ParseMode.MARKDOWN
        )


def _calendar_id_for_row(row: db.EventRow) -> str:
    """Phase 3 — resolve calendar_id để delete/edit/sync đúng Calendar đã tạo.

    Bot insert event vào Calendar khác primary (vd TEAM Calendar cho group
    mode). Khi delete/patch sau đó, KHÔNG pass calendar_id → API mặc định
    vào primary → event không có ở primary → treat thành "decline" thay vì
    xoá thật. Helper này resolve calendar_id từ row.chat_mode.
    """
    from bot.permissions import CALENDAR_PERSONAL_ID, CALENDAR_TEAM_ID
    if (row.chat_mode or "personal") == "group":
        cal = CALENDAR_TEAM_ID()
        if cal:
            return cal
    return CALENDAR_PERSONAL_ID()


def _format_field_diff(
    old_row: db.EventRow, new_row: db.EventRow, field: str,
) -> str:
    """Helper Phase 3: render 1 dòng diff "✏️ Field: old → new" cho group reply."""
    label_map = {
        "time": "🕐 Giờ/ngày",
        "dur": "⏱ Thời lượng",
        "topic": "🏷 Tiêu đề",
        "att_add": "➕ Khách (thêm)",
        "att_rm": "➖ Khách (bỏ)",
    }
    label = label_map.get(field)
    if label is None:
        return ""
    if field == "time":
        old_v = old_row.start_dt.strftime("%H:%M %d/%m/%Y")
        new_v = new_row.start_dt.strftime("%H:%M %d/%m/%Y")
    elif field == "dur":
        old_v = f"{old_row.duration_min} phút"
        new_v = f"{new_row.duration_min} phút"
    elif field == "topic":
        old_v = old_row.topic
        new_v = new_row.topic
    elif field in ("att_add", "att_rm"):
        old_v = f"{len(old_row.attendees)} người"
        new_v = f"{len(new_row.attendees)} người"
    else:
        return ""
    return f"✏️ {label}: {old_v} → {new_v}"


def _apply_edit(row: db.EventRow, field: str, new_value, *, notify: bool = True) -> None:
    """Sync one field across Zoom (if applicable), Google Calendar, and local DB."""
    is_personal = row.provider == "meet"
    # Compute the "after" state we need for side effects
    topic = row.topic
    start_local = row.start_local
    duration_min = row.duration_min
    agenda = row.agenda
    attendees = list(row.attendees)

    if field == "time":
        start_local = new_value
    elif field == "dur":
        duration_min = int(new_value)
    elif field == "topic":
        topic = new_value
    elif field == "ag":
        agenda = new_value
    elif field in ("att_add", "att_rm"):
        attendees = list(new_value)

    from datetime import datetime as _dt
    start_dt = _dt.fromisoformat(start_local)
    end_dt = start_dt + timedelta(minutes=duration_min)

    # Zoom: update if time/duration/topic/agenda changed — skip for HY (no Zoom)
    if not is_personal and field in ("time", "dur", "topic", "ag"):
        _zoom.update_meeting(
            row.zoom_meeting_id,
            topic=topic if field == "topic" else None,
            start_local_iso=start_local if field == "time" else None,
            duration_min=duration_min if field == "dur" else None,
            agenda=agenda if field == "ag" else None,
        )

    # Calendar: always update for any field
    cmd_like = formatter.event_to_parsed(
        db.EventRow(
            id=row.id, topic=topic, start_local=start_local,
            duration_min=duration_min, agenda=agenda, attendees=attendees,
            recurring=row.recurring, zoom_meeting_id=row.zoom_meeting_id,
            zoom_join_url=row.zoom_join_url, zoom_passcode=row.zoom_passcode,
            calendar_event_id=row.calendar_event_id,
            calendar_event_link=row.calendar_event_link,
            status=row.status, created_at=row.created_at, updated_at=row.updated_at,
            provider=row.provider, meet_join_url=row.meet_join_url,
        )
    )
    if is_personal:
        new_description = formatter.format_personal_calendar_description(
            cmd=cmd_like, meet_link=row.meet_join_url,
        )
        summary_update = f"[HY] {topic}" if field == "topic" else None
    else:
        # Phase 3 fix (2026-05-05): khi sửa lịch group, description PHẢI giữ
        # đúng identity của người TẠO (creator) — không phải người đang sửa.
        # Lookup creator_cfg từ row.created_by_user_id; nếu là lịch group + có
        # creator_cfg → dùng template group (giữ nguyên tên/team/chữ ký creator).
        # Fallback (lịch personal cũ trước Phase 3 hoặc creator không còn trong
        # USERS): dùng template cũ với CONTACT_NAME default.
        creator_cfg = (
            get_user(row.created_by_user_id) if row.created_by_user_id else None
        )
        is_group_event = (row.chat_mode or "personal") == "group"
        if is_group_event and creator_cfg is not None:
            new_description = formatter.format_group_calendar_description(
                cmd=cmd_like,
                zoom_join_url=row.zoom_join_url,
                zoom_meeting_id=int(row.zoom_meeting_id),
                zoom_passcode=row.zoom_passcode,
                creator_display_name=creator_cfg.display_name,
                creator_team=creator_cfg.team,
                creator_signature=creator_cfg.signature,
            )
            title_prefix = creator_cfg.title_prefix
        else:
            new_description = formatter.format_calendar_description(
                cmd=cmd_like,
                zoom_join_url=row.zoom_join_url,
                zoom_meeting_id=int(row.zoom_meeting_id),
                zoom_passcode=row.zoom_passcode,
            )
            title_prefix = "[John Academy] "
        summary_update = f"{title_prefix}{topic}" if field == "topic" else None
    _get_calendar().patch_event(
        row.calendar_event_id,
        calendar_id=_calendar_id_for_row(row),
        summary=summary_update,
        description=new_description,
        start_local_iso=start_local if field in ("time", "dur") else None,
        end_local_iso=end_dt.isoformat(timespec="seconds") if field in ("time", "dur") else None,
        attendee_emails=attendees if field in ("att_add", "att_rm") else None,
        notify=notify,
    )

    # DB
    updates: dict = {}
    if field == "time":
        updates["start_local"] = start_local
    elif field == "dur":
        updates["duration_min"] = duration_min
    elif field == "topic":
        updates["topic"] = topic
    elif field == "ag":
        updates["agenda"] = agenda
    elif field in ("att_add", "att_rm"):
        updates["attendees"] = attendees
    db.update_event_fields(row.id, **updates)


# ── Action: delete ─────────────────────────────────────────────────────────────
async def _do_delete(query, event_id: int, *, notify: bool = True,
                     update: Update | None = None) -> None:
    row = db.get_event(event_id)
    if row is None or row.status != "active":
        await query.edit_message_text("⚠️ Lịch này không còn.")
        return
    # Phase 3 — permission check (skip nếu không có update để resolve ctx)
    perm_ctx: RequestContext | None = None
    if update is not None:
        perm_ctx = resolve_context(update)
        ok, reason = can_modify_event(perm_ctx, row)
        if not ok:
            await query.edit_message_text(reason, parse_mode=ParseMode.MARKDOWN)
            audit(perm_ctx, "delete_event", params=f"id={event_id}",
                  result="reject", error_message=reason)
            return
    is_personal = row.provider == "meet"
    mail_note = "Khách đã nhận email huỷ." if notify else "Không gửi email cho khách."
    step_label = "Calendar" if is_personal else "Zoom + Calendar"
    await query.edit_message_text(f"⏳ Đang xoá {step_label}… ({mail_note})")
    if not is_personal:
        try:
            _zoom.delete_meeting(row.zoom_meeting_id)
        except Exception:
            log.exception("Zoom delete failed (soft-continuing)")
    try:
        _get_calendar().delete_event(
            row.calendar_event_id,
            calendar_id=_calendar_id_for_row(row),
            notify=notify,
        )
    except Exception:
        log.exception("Calendar delete failed (soft-continuing)")
    db.mark_deleted(event_id)
    # Phase 3 — group mode: gộp actor + thời gian (đã xoá) vào reply.
    actor_block = ""
    if (row.chat_mode or "personal") == "group" and perm_ctx is not None:
        actor_cfg = get_user(perm_ctx.user_id)
        team_str = f" ({actor_cfg.team})" if actor_cfg else ""
        time_str = (
            f"{row.start_dt.strftime('%H:%M %d/%m/%Y')} "
            f"({row.duration_min} phút)"
        )
        actor_block = (
            f"\n👤 *Xoá bởi:* {perm_ctx.display_name}{team_str}\n"
            f"⏰ Thời gian: {time_str}"
        )
    await query.edit_message_text(
        f"🗑 Đã xoá lịch *{row.topic}* (id={event_id}). {mail_note}"
        + actor_block,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("↩️ Quay lại list", callback_data="back_list")]]
        ),
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Single-occurrence apply (recurring series) ────────────────────────────────
def _apply_occurrence_edit(
    row: db.EventRow, occ: dict, field: str, new_value, *, notify: bool = True
) -> None:
    """Edit time OR duration of one buổi in a recurring series."""
    orig_start = datetime.fromisoformat(occ["start_local"])

    if field == "time":
        start_dt = datetime.fromisoformat(new_value)
        duration_min = row.duration_min
    elif field == "dur":
        start_dt = orig_start
        duration_min = int(new_value)
    else:
        raise RuntimeError(f"Field {field} chưa hỗ trợ ở cấp occurrence")

    start_iso = start_dt.isoformat(timespec="seconds")
    end_iso = (start_dt + timedelta(minutes=duration_min)).isoformat(timespec="seconds")

    is_personal = row.provider == "meet"
    # Zoom occurrence update — skip for HY (no Zoom backing)
    if not is_personal and occ["zoom_occ_id"]:
        _zoom.update_occurrence(
            row.zoom_meeting_id,
            occ["zoom_occ_id"],
            start_local_iso=start_iso if field == "time" else None,
            duration_min=duration_min if field == "dur" else None,
        )

    # Calendar instance patch
    if occ["cal_instance_id"]:
        _get_calendar().patch_instance(
            occ["cal_instance_id"],
            calendar_id=_calendar_id_for_row(row),
            start_local_iso=start_iso if field in ("time", "dur") else None,
            end_local_iso=end_iso,
            notify=notify,
        )


async def _show_occurrence_picker(
    query, ctx: ContextTypes.DEFAULT_TYPE, event_id: int, *, action: str,
) -> None:
    """Fetch fresh occurrence list, stash in chat_data, show buttons."""
    row = db.get_event(event_id)
    if not row or not row.recurring:
        await query.edit_message_text("⚠️ Lịch không phải recurring.")
        return
    await query.edit_message_text("⏳ Đang lấy danh sách buổi…")
    try:
        occs = _fetch_occurrences(row)
    except Exception as e:
        log.exception("Fetch occurrences failed")
        await query.edit_message_text(
            f"❌ Không lấy được danh sách buổi: `{e}`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    ctx.chat_data.setdefault("occurrences", {})[event_id] = occs

    prefix = "del_occ_ask" if action == "xoá" else "ed_occ_sel"
    text = formatter.format_occurrence_list(row, occs, action)
    await query.edit_message_text(
        text,
        reply_markup=_occurrence_keyboard(event_id, occs, prefix),
        parse_mode=ParseMode.MARKDOWN,
    )


async def _do_delete_occurrence(
    query, event_id: int, occ: dict, *, notify: bool = True
) -> None:
    row = db.get_event(event_id)
    if not row:
        await query.edit_message_text("⚠️ Lịch không tồn tại.")
        return
    is_personal = row.provider == "meet"
    mail_note = "Khách đã nhận email huỷ buổi." if notify else "Không gửi email cho khách."
    step_label = "Calendar" if is_personal else "Zoom + Calendar"
    await query.edit_message_text(f"⏳ Đang huỷ 1 buổi trên {step_label}… ({mail_note})")
    try:
        if not is_personal and occ["zoom_occ_id"]:
            try:
                _zoom.delete_occurrence(row.zoom_meeting_id, occ["zoom_occ_id"])
            except Exception:
                log.exception("Zoom occurrence delete failed")
        if occ["cal_instance_id"]:
            try:
                _get_calendar().cancel_instance(
                    occ["cal_instance_id"],
                    calendar_id=_calendar_id_for_row(row),
                    notify=notify,
                )
            except Exception:
                log.exception("Calendar instance cancel failed")
        db.add_cancelled_occurrence(event_id, occ["start_local"])
        date_str = formatter.format_occurrence_date(occ)
        await query.edit_message_text(
            f"✅ Đã huỷ buổi *{date_str}* trong lịch lặp *{row.topic}*. "
            f"{mail_note}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("↩️ Quay lại lịch",
                                      callback_data=f"back_det:{event_id}")],
            ]),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        log.exception("Cancel occurrence failed")
        await query.edit_message_text(
            f"❌ Lỗi huỷ 1 buổi: `{e}`", parse_mode=ParseMode.MARKDOWN
        )


# ── External (Calendar) flow ──────────────────────────────────────────────────
async def _show_ext_detail(query, ctx: ContextTypes.DEFAULT_TYPE,
                            idx: int | None = None, *, prefix: str = "") -> None:
    """Render external detail view. If idx given, stash current_ext from list."""
    if idx is not None:
        cache = ctx.chat_data.get("list_externals") or []
        if idx >= len(cache):
            await query.edit_message_text(
                "⚠️ Danh sách Calendar đã cũ. Gõ /list lại giúp em."
            )
            return
        ctx.chat_data["current_ext"] = cache[idx]
    occ = ctx.chat_data.get("current_ext")
    if not occ:
        await query.edit_message_text(
            "⚠️ Không còn dữ liệu lịch Calendar. Gõ /list lại giúp em."
        )
        return
    text = prefix + formatter.format_external_detail(occ)
    await query.edit_message_text(
        text,
        reply_markup=_ext_detail_keyboard(idx or 0),
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )


def _parse_ext_edit(occ: dict, field: str, text: str):
    """Same shape as _parse_edit but works on an external occ dict."""
    from datetime import datetime as _dt
    base = _dt.fromisoformat(occ["occurrence_iso"])
    if field == "time":
        dt = parse_edit_time(text, base=base)
        return (
            dt.isoformat(timespec="seconds"),
            f"🕐 {dt.day}/{dt.month}/{dt.year} {dt.hour:02d}:{dt.minute:02d}",
        )
    if field == "dur":
        n = parse_edit_duration(text)
        return n, f"⏱ {n} phút"
    if field == "att_add":
        emails = parse_edit_emails(text)
        existing = occ.get("attendees") or []
        merged = list(dict.fromkeys([*existing, *emails]))
        added = [e for e in emails if e not in existing]
        if not added:
            raise ParseError("Các email này đã có trong lịch rồi.")
        return (
            merged,
            "➕ Thêm: " + ", ".join(added) + f"\n→ Sau khi sửa: {len(merged)} khách",
        )
    if field == "att_rm":
        emails = parse_edit_emails(text)
        existing = occ.get("attendees") or []
        remaining = [e for e in existing if e not in emails]
        removed = [e for e in emails if e in existing]
        if not removed:
            raise ParseError("Không thấy email nào trong list để bỏ.")
        return (
            remaining,
            "➖ Bỏ: " + ", ".join(removed) + f"\n→ Còn lại: {len(remaining)} khách",
        )
    if field == "topic":
        v = parse_edit_plain(text, label="Tên lịch")
        return v, f"🏷 {v}"
    if field == "ag":
        v = parse_edit_plain(text, label="Nội dung")
        return v, f"🎯 {v}"
    raise ParseError(f"Field không hợp lệ: {field}")


async def _handle_ext_edit_value(
    update: Update, ctx: ContextTypes.DEFAULT_TYPE, text: str, edit_mode: dict,
) -> None:
    occ = ctx.chat_data.get("current_ext")
    if not occ:
        ctx.chat_data.pop("ext_edit_mode", None)
        await update.message.reply_text(
            "⚠️ Không còn dữ liệu lịch. Gõ /list lại giúp em."
        )
        return
    field = edit_mode["field"]
    try:
        new_value, display = _parse_ext_edit(occ, field, text)
    except ParseError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return
    ctx.chat_data.pop("ext_edit_mode", None)
    ctx.chat_data["pending_ext_edit"] = {
        "field": field, "new_value": new_value, "display": display,
    }
    preview = formatter.format_external_edit_preview(occ, field, display)
    await update.message.reply_text(
        preview,
        reply_markup=_ext_edit_confirm_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


def _apply_ext_edit(occ: dict, field: str, new_value, *, notify: bool = True) -> dict:
    """Patch the Calendar event. Returns updated occ dict for local cache."""
    start_local = occ["occurrence_iso"]
    duration_min = occ["duration_min"]
    topic = occ["topic"]
    attendees = list(occ.get("attendees") or [])

    if field == "time":
        start_local = new_value
    elif field == "dur":
        duration_min = int(new_value)
    elif field == "topic":
        topic = new_value
    elif field in ("att_add", "att_rm"):
        attendees = list(new_value)
    # agenda goes straight into description; no reformatting needed

    start_dt = datetime.fromisoformat(start_local)
    end_dt = start_dt + timedelta(minutes=duration_min)

    patch_kwargs: dict = {"notify": notify}
    if field == "topic":
        patch_kwargs["summary"] = topic
    if field == "ag":
        patch_kwargs["description"] = new_value
    if field in ("time", "dur"):
        patch_kwargs["start_local_iso"] = start_local
        patch_kwargs["end_local_iso"] = end_dt.isoformat(timespec="seconds")
    if field in ("att_add", "att_rm"):
        patch_kwargs["attendee_emails"] = attendees

    _get_calendar().patch_event(occ["calendar_event_id"], **patch_kwargs)

    updated = dict(occ)
    if field == "time":
        updated["occurrence_iso"] = start_local
    elif field == "dur":
        updated["duration_min"] = duration_min
    elif field == "topic":
        updated["topic"] = topic
    elif field == "ag":
        updated["agenda"] = new_value
    elif field in ("att_add", "att_rm"):
        updated["attendees"] = attendees
    return updated


async def _do_ext_edit(query, ctx: ContextTypes.DEFAULT_TYPE, *, notify: bool) -> None:
    pending = ctx.chat_data.get("pending_ext_edit")
    occ = ctx.chat_data.get("current_ext")
    if not pending or not occ:
        await query.edit_message_text("⚠️ Phiên sửa đã hết hạn.")
        return
    mail_note = "Khách sẽ nhận email cập nhật." if notify else "Không gửi email cho khách."
    await query.edit_message_text(
        f"⏳ Đang apply thay đổi lên Calendar… ({mail_note})"
    )
    try:
        updated = _apply_ext_edit(
            occ, pending["field"], pending["new_value"], notify=notify,
        )
        ctx.chat_data["current_ext"] = updated
        ctx.chat_data.pop("pending_ext_edit", None)
        await query.edit_message_text(
            f"✅ Đã cập nhật. {mail_note}\n\n"
            + formatter.format_external_detail(updated),
            reply_markup=_ext_detail_keyboard(0),
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.exception("External edit apply failed")
        await query.edit_message_text(
            f"❌ Lỗi apply: `{e}`", parse_mode=ParseMode.MARKDOWN,
        )


async def _do_ext_delete(query, ctx: ContextTypes.DEFAULT_TYPE, *, notify: bool) -> None:
    occ = ctx.chat_data.get("current_ext")
    if not occ:
        await query.edit_message_text("⚠️ Không còn dữ liệu lịch.")
        return
    mail_note = "Khách đã nhận email huỷ." if notify else "Không gửi email cho khách."
    await query.edit_message_text(f"⏳ Đang xoá Calendar event… ({mail_note})")
    try:
        _get_calendar().delete_event(occ["calendar_event_id"], notify=notify)
    except Exception as e:
        log.exception("External delete failed")
        await query.edit_message_text(
            f"❌ Lỗi xoá: `{e}`", parse_mode=ParseMode.MARKDOWN,
        )
        return
    ctx.chat_data.pop("current_ext", None)
    await query.edit_message_text(
        f"🗑 Đã xoá lịch *{occ['topic']}* trên Calendar. {mail_note}",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("↩️ Quay lại list", callback_data="back_list")]]
        ),
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Helpers ────────────────────────────────────────────────────────────────────
async def _show_detail(query, event_id: int, *, prefix: str = "") -> None:
    row = db.get_event(event_id)
    if row is None:
        await query.edit_message_text("⚠️ Lịch không tồn tại.")
        return
    text = prefix + formatter.format_event_detail(row)
    drift_banner = ""
    has_drift = False
    if row.status == "active":
        try:
            diffs = compute_drift(row)
            if diffs:
                has_drift = True
                changed = ", ".join(diffs.keys())
                drift_banner = (
                    f"\n\n⚠️ *Calendar đã khác DB* ({changed}). "
                    f"Bấm 🔄 Sync để đồng bộ."
                )
        except Exception:
            log.exception("Drift check failed (soft-continuing)")
    await query.edit_message_text(
        text + drift_banner,
        reply_markup=_detail_keyboard(
            event_id,
            show_edit=(row.status == "active"),
            show_sync=has_drift,
        ),
        parse_mode=ParseMode.MARKDOWN,
        disable_web_page_preview=True,
    )
