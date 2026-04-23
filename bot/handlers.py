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

from bot import config, db, formatter
from bot.calendar_client import CalendarClient
from bot.parser import (
    CloneOverrides,
    CloneSpec,
    ParseError,
    ParsedCommand,
    TargetSpec,
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


# ── /start, /help ──────────────────────────────────────────────────────────────
_HELP_TEXT = (
    "👋 Em là *JA Scheduler Bot* — tự động tạo Zoom + Google Calendar invite.\n\n"
    "*Tạo lịch mới:* nhắn theo format:\n\n"
    "```\n"
    "Tạo lịch \"Tư vấn OKRs - Chị Lan\":\n"
    "- Thời gian: 22/4/2026 14:00\n"
    "- Thời lượng: 30 phút\n"
    "- Nội dung: Tư vấn gói Coaching OKRs\n"
    "- Khách: lan@abc.com\n"
    "```\n\n"
    "*Recurring:* thêm `hàng tuần` + `X tuần liên tiếp`:\n\n"
    "```\n"
    "- Thời gian: 8h30 sáng thứ 4 hàng tuần trong 12 tuần liên tiếp bắt đầu từ 20/5/2026\n"
    "```\n\n"
    "*Sửa nhanh lịch mới nhất* (hoặc thêm `#id` để target lịch cụ thể):\n"
    "```\n"
    "sửa giờ 15h30\n"
    "sửa giờ 15h30 25/4/2026 #5\n"
    "sửa thời lượng 45 phút\n"
    "sửa tên Tư vấn OKRs v2\n"
    "sửa nội dung Nội dung mới\n"
    "thêm khách a@x.vn, b@y.vn\n"
    "bỏ khách a@x.vn\n"
    "xoá lịch\n"
    "```\n\n"
    "*Quản lý đầy đủ:* gõ /list để xem 10 lịch gần nhất, bấm số để vào chi tiết / sửa / xoá "
    "(bao gồm xoá hoặc sửa 1 buổi trong lịch lặp).\n\n"
    "*Tìm & lật trang trong /list:*\n"
    "```\n"
    "/list 2                  ← trang 2 (mỗi trang 10 lịch)\n"
    "/list OKRs               ← lọc theo từ khoá tên/nội dung\n"
    "/list khách lan@abc.com  ← lọc theo email khách\n"
    "/list tuần này           ← trong tuần hiện tại\n"
    "/list tuần sau | tuần trước\n"
    "/list hôm nay | mai | hôm qua\n"
    "/list tháng này | tháng 5 | tháng 5/2026\n"
    "/list 27/4               ← ngày cụ thể\n"
    "/list 27/4-4/5           ← khoảng ngày\n"
    "/list OKRs 2             ← từ khoá + trang (trang luôn là số cuối)\n"
    "```\n\n"
    "*Sửa/xoá lịch cũ bằng tên (không cần nhớ id):*\n"
    "```\n"
    "sửa giờ 15h30 \"Tư vấn OKRs\" ngày 25/4\n"
    "xoá lịch \"Tư vấn OKRs\" ngày 25/4\n"
    "xoá lịch khách lan@abc.com\n"
    "sửa thời lượng 45 phút \"Mentor MBOs\"\n"
    "```\n"
    "Nếu nhiều lịch khớp → bot hiện list để chị bấm số chọn.\n\n"
    "*Clone lịch cũ* (copy nhanh rồi chỉnh giờ/khách):\n"
    "```\n"
    "tạo lịch giống #5\n"
    "tạo lịch giống #5 nhưng ngày 27/4 15h\n"
    "tạo lịch giống \"Tư vấn OKRs\" nhưng ngày mai, khách a@x.vn\n"
    "tạo lịch giống #3 nhưng tên \"OKRs v2\", thêm khách b@y.vn\n"
    "```\n\n"
    "*Cảnh báo trùng lịch:* khi tạo/clone/đổi giờ, nếu overlap với lịch khác, "
    "bot hiện cảnh báo. Chị vẫn confirm được nếu cố ý trùng.\n\n"
    "*Kéo thả trên Calendar:* chị kéo thả lịch thoải mái trên Google Calendar UI. "
    "Sau đó gõ `/sync` (lịch mới nhất) hoặc `/sync <id>` — bot sẽ so sánh và cập nhật Zoom + DB theo Calendar. "
    "Khi vào chi tiết qua /list, bot cũng tự phát hiện drift và hiện nút 🔄 Sync."
)


async def cmd_start(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await _reject(update)
        return
    await update.message.reply_text(_HELP_TEXT, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await _reject(update)
        return
    await update.message.reply_text(_HELP_TEXT, parse_mode=ParseMode.MARKDOWN)


# ── /list ──────────────────────────────────────────────────────────────────────
async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Show paginated / filtered list. Without args → legacy "10 lịch gần nhất"."""
    if not _is_allowed(update):
        await _reject(update)
        return

    raw_args = " ".join(ctx.args or []).strip() if hasattr(ctx, "args") else ""
    if not raw_args:
        # Legacy path: no pagination header, preserves exact old UX
        rows = db.list_recent(limit=10)
        text = formatter.format_list(rows)
        markup = _list_keyboard(rows)
        ctx.chat_data.pop("list_query", None)
        await update.message.reply_text(
            text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN
        )
        return

    try:
        query = parse_list_args(raw_args)
    except ParseError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return

    await _render_list_query(update.message, ctx, query)


async def _render_list_query(message_or_query, ctx: ContextTypes.DEFAULT_TYPE,
                              query) -> None:
    """Shared renderer for /list + pagination callbacks. Works with Message or CallbackQuery."""
    total = db.count_events(
        topic_contains=query.topic,
        attendee_contains=query.attendee,
        date_from=query.date_from,
        date_to=query.date_to,
    )
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
    )
    text = formatter.format_list(
        rows,
        total=total,
        page=query.page,
        page_size=query.page_size,
        query_desc=query.describe_vi(),
    )
    markup = _paged_list_keyboard(rows, query, total_pages)

    # Stash current query so pagination callbacks can re-render
    ctx.chat_data["list_query"] = {
        "topic": query.topic,
        "attendee": query.attendee,
        "date_from": query.date_from,
        "date_to": query.date_to,
        "page": query.page,
        "page_size": query.page_size,
    }

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


def _paged_list_keyboard(rows: list[db.EventRow], query, total_pages: int
                          ) -> InlineKeyboardMarkup | None:
    """List keyboard with numbered row-pick buttons + page navigation row."""
    if not rows and total_pages <= 1:
        return None
    buttons = [InlineKeyboardButton(str(i), callback_data=f"ls_sel:{r.id}")
               for i, r in enumerate(rows, 1)]
    rows_kb = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]

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


# ── Text handler: create-parse OR edit-value (depending on state) ─────────────
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await _reject(update)
        return
    text = update.message.text or ""
    low = text.strip().lower()

    # 0) Escape hatch: "huỷ" / "hủy" / "bỏ" / "cancel" clears any pending state.
    if low in {"huỷ", "hủy", "bỏ", "bo", "huy", "cancel", "/cancel"}:
        had = any(k in ctx.chat_data for k in
                  ("pending", "pending_edit", "pending_delete", "pending_sync",
                   "edit_mode", "pending_quick_disambig",
                   "pending_clone_disambig"))
        for k in ("pending", "pending_edit", "pending_delete",
                  "pending_sync", "edit_mode", "occurrences",
                  "pending_quick_disambig", "pending_clone_disambig"):
            ctx.chat_data.pop(k, None)
        await update.message.reply_text(
            "✅ Đã huỷ trạng thái chờ." if had else "ℹ️ Không có gì đang chờ."
        )
        return

    # 1) Edit-value mode takes priority (chị đang điền giá trị mới cho field)
    edit_mode = ctx.chat_data.get("edit_mode")
    if edit_mode:
        await _handle_edit_value(update, ctx, text, edit_mode)
        return

    # 2) Quick-edit supersedes any pending_edit (newer command wins — better UX
    #    than forcing chị phải bấm nút cũ).
    try:
        quick = parse_quick_edit(text)
    except ParseError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return
    if quick is not None:
        ctx.chat_data.pop("pending_edit", None)
        ctx.chat_data.pop("pending_delete", None)
        await _handle_quick_edit(update, ctx, *quick)
        return

    # 3) Pending-edit blocks only non-quick-edit free text
    if ctx.chat_data.get("pending_edit"):
        await update.message.reply_text(
            "⚠️ Đang chờ xác nhận sửa. Bấm ✅/❌, nhắn lệnh sửa mới, hoặc nhắn `huỷ`."
        )
        return

    # 4) Clone flow must run BEFORE the generic create flow — "tạo lịch giống #5"
    #    would otherwise be swallowed by parse_command with garbage topic.
    try:
        clone = parse_clone(text)
    except ParseError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return
    if clone is not None:
        await _handle_clone(update, ctx, clone)
        return

    # 5) Default: create flow
    if "tạo lịch" not in text.lower() and "tao lich" not in text.lower():
        await update.message.reply_text(
            "Em chưa hiểu. Gõ /help để xem ví dụ hoặc /list để quản lý lịch cũ."
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

    ctx.chat_data["pending"] = cmd
    conflicts = _collect_conflicts_for(cmd)
    preview = formatter.format_confirm_preview(cmd) + formatter.format_conflict_warning(conflicts)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Xác nhận tạo", callback_data="cr_confirm"),
        InlineKeyboardButton("❌ Huỷ", callback_data="cr_cancel"),
    ]])
    await update.message.reply_text(preview, reply_markup=keyboard,
                                     parse_mode=ParseMode.MARKDOWN)


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
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Xác nhận tạo", callback_data="cr_confirm"),
        InlineKeyboardButton("❌ Huỷ", callback_data="cr_cancel"),
    ]])
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
        await update.message.reply_text("⚠️ Lịch này không còn tồn tại. Gõ /list để xem.")
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
            await update.message.reply_text("⚠️ Danh sách buổi đã cũ. Gõ /list làm lại.")
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
    """Return (new_value, human_display_string)."""
    if field == "time":
        dt = parse_edit_time(text, base=row.start_dt)
        return dt.isoformat(timespec="seconds"), f"🕐 {dt.day}/{dt.month}/{dt.year} {dt.hour:02d}:{dt.minute:02d}"
    if field == "dur":
        n = parse_edit_duration(text)
        return n, f"⏱ {n} phút"
    if field == "att_add":
        emails = parse_edit_emails(text)
        merged = list(dict.fromkeys([*row.attendees, *emails]))
        added = [e for e in emails if e not in row.attendees]
        if not added:
            raise ParseError("Các email này đã có trong lịch rồi.")
        return merged, "➕ Thêm: " + ", ".join(added) + f"\n→ Sau khi sửa: {len(merged)} khách"
    if field == "att_rm":
        emails = parse_edit_emails(text)
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


# ── Drag-drop sync: pull Calendar state into DB + Zoom ────────────────────────
_TITLE_PREFIX = "[John Academy] "


def _parse_cal_datetime(s: str) -> datetime:
    """Strip timezone offset from 'YYYY-MM-DDTHH:MM:SS+07:00' → naive local."""
    dt = datetime.fromisoformat(s)
    return dt.replace(tzinfo=None)


def compute_drift(row: db.EventRow) -> dict:
    """Return dict of {field: (db_value, cal_value)} for fields out of sync."""
    event = _get_calendar().get_event(row.calendar_event_id)
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

    # Zoom: push any time/duration/topic change
    if zoom_kwargs:
        _zoom.update_meeting(row.zoom_meeting_id, **zoom_kwargs)

    # DB: last-write-wins
    if updates:
        db.update_event_fields(row.id, **updates)


async def cmd_sync(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await _reject(update)
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
        await update.message.reply_text(
            "⚠️ Không tìm thấy lịch khớp. "
            "Thử `#id` (VD `sửa giờ 15h #3`), thêm `\"tên lịch\"` hoặc `ngày DD/MM`, "
            "hoặc gõ /list để xem lại."
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
                "Muốn huỷ 1 buổi thôi thì /list → bấm lịch → 🗑 → *Chỉ 1 buổi*."
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
    zoom_detail = _zoom.get_meeting(row.zoom_meeting_id)
    zoom_occs = sorted(
        zoom_detail.get("occurrences", []),
        key=lambda o: o["start_time"],
    )
    cal_instances = _get_calendar().list_instances(row.calendar_event_id)
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
    if not _is_allowed(update):
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
            await query.edit_message_text("❌ Đã huỷ tạo lịch. Gửi lệnh mới khi cần nhé.")
            return

        if data == "back_list":
            # Prefer restoring paged/filtered list if one is active, else legacy view
            if ctx.chat_data.get("list_query"):
                await _render_list_query(query, ctx, _query_from_chat_data(ctx))
                return
            rows = db.list_recent(limit=10)
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
            await _render_list_query(query, ctx, q)
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
            await query.edit_message_text(
                f"✏️ *{formatter.edit_prompt(field)}*\n(id={event_id})",
                parse_mode=ParseMode.MARKDOWN,
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
                await query.edit_message_text("⚠️ Danh sách đã cũ. Gõ /list làm lại.")
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
            await _do_delete(query, event_id, notify=notify)
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
            await query.edit_message_text("⏳ Đang sync Zoom + DB theo Calendar…")
            try:
                _apply_drift_sync(row, pending["diffs"])
                updated = db.get_event(event_id)
                await query.edit_message_text(
                    "✅ Đã đồng bộ.\n\n" + formatter.format_event_detail(updated),
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
                await query.edit_message_text("⚠️ Danh sách đã cũ. Gõ /list làm lại.")
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
                await query.edit_message_text("⚠️ Danh sách đã cũ. Gõ /list làm lại.")
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
    await query.edit_message_text("⏳ Đang tạo Zoom + Calendar event…")
    try:
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
        description = formatter.format_calendar_description(
            cmd=cmd,
            zoom_join_url=zoom.join_url,
            zoom_meeting_id=zoom.meeting_id,
            zoom_passcode=zoom.passcode,
        )
        event = _get_calendar().create_event(
            summary=f"[John Academy] {cmd.topic}",
            description=description,
            start_local_iso=start_iso,
            end_local_iso=end_local_iso,
            attendee_emails=cmd.attendees,
            rrule=rrule,
        )
        event_id = db.insert_event(
            topic=cmd.topic,
            start_local=start_iso,
            duration_min=cmd.duration_min,
            agenda=cmd.agenda,
            attendees=cmd.attendees,
            recurring=cmd.recurring,
            zoom_meeting_id=str(zoom.meeting_id),
            zoom_join_url=zoom.join_url,
            zoom_passcode=zoom.passcode,
            calendar_event_id=event.event_id,
            calendar_event_link=event.html_link,
        )
        ctx.chat_data["last_created_id"] = event_id
        reply = formatter.format_success_reply(
            cmd=cmd,
            zoom_join_url=zoom.join_url,
            zoom_meeting_id=zoom.meeting_id,
            zoom_passcode=zoom.passcode,
            calendar_event_link=event.html_link,
        )
        await query.edit_message_text(
            reply + f"\n\n🆔 *DB id:* `{event_id}` — gõ /list để sửa/xoá.",
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
                raise RuntimeError("Danh sách buổi đã cũ, gõ /list làm lại.")
            _apply_occurrence_edit(row, occs[occ_idx], field, new_value, notify=notify)
        else:
            _apply_edit(row, field, new_value, notify=notify)
        ctx.chat_data.pop("pending_edit", None)
        updated = db.get_event(event_id)
        await query.edit_message_text(
            f"✅ Đã cập nhật. {mail_note}\n\n" + formatter.format_event_detail(updated),
            reply_markup=_detail_keyboard(event_id),
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.exception("Failed to apply edit")
        await query.edit_message_text(
            f"❌ Lỗi apply: `{e}`", parse_mode=ParseMode.MARKDOWN
        )


def _apply_edit(row: db.EventRow, field: str, new_value, *, notify: bool = True) -> None:
    """Sync one field across Zoom, Google Calendar, and local DB."""
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

    # Zoom: update if time/duration/topic/agenda changed
    if field in ("time", "dur", "topic", "ag"):
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
        )
    )
    new_description = formatter.format_calendar_description(
        cmd=cmd_like,
        zoom_join_url=row.zoom_join_url,
        zoom_meeting_id=int(row.zoom_meeting_id),
        zoom_passcode=row.zoom_passcode,
    )
    _get_calendar().patch_event(
        row.calendar_event_id,
        summary=f"[John Academy] {topic}" if field == "topic" else None,
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
async def _do_delete(query, event_id: int, *, notify: bool = True) -> None:
    row = db.get_event(event_id)
    if row is None or row.status != "active":
        await query.edit_message_text("⚠️ Lịch này không còn.")
        return
    mail_note = "Khách đã nhận email huỷ." if notify else "Không gửi email cho khách."
    await query.edit_message_text(f"⏳ Đang xoá Zoom + Calendar… ({mail_note})")
    try:
        _zoom.delete_meeting(row.zoom_meeting_id)
    except Exception:
        log.exception("Zoom delete failed (soft-continuing)")
    try:
        _get_calendar().delete_event(row.calendar_event_id, notify=notify)
    except Exception:
        log.exception("Calendar delete failed (soft-continuing)")
    db.mark_deleted(event_id)
    await query.edit_message_text(
        f"🗑 Đã xoá lịch *{row.topic}* (id={event_id}). {mail_note}",
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

    # Zoom occurrence update
    if occ["zoom_occ_id"]:
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
    mail_note = "Khách đã nhận email huỷ buổi." if notify else "Không gửi email cho khách."
    await query.edit_message_text(f"⏳ Đang huỷ 1 buổi trên Zoom + Calendar… ({mail_note})")
    try:
        if occ["zoom_occ_id"]:
            try:
                _zoom.delete_occurrence(row.zoom_meeting_id, occ["zoom_occ_id"])
            except Exception:
                log.exception("Zoom occurrence delete failed")
        if occ["cal_instance_id"]:
            try:
                _get_calendar().cancel_instance(occ["cal_instance_id"], notify=notify)
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
