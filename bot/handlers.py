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
    ParseError,
    parse_command,
    parse_edit_duration,
    parse_edit_emails,
    parse_edit_plain,
    parse_edit_time,
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
async def cmd_list(update: Update, _ctx: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_allowed(update):
        await _reject(update)
        return
    rows = db.list_recent(limit=10)
    text = formatter.format_list(rows)
    markup = _list_keyboard(rows)
    await update.message.reply_text(text, reply_markup=markup, parse_mode=ParseMode.MARKDOWN)


def _list_keyboard(rows: list[db.EventRow]) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    # Row of numbered buttons, 5 per row
    buttons = [InlineKeyboardButton(str(i), callback_data=f"ls_sel:{r.id}")
               for i, r in enumerate(rows, 1)]
    keyboard = [buttons[i:i + 5] for i in range(0, len(buttons), 5)]
    return InlineKeyboardMarkup(keyboard)


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
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Xoá thật", callback_data=f"del_yes:{event_id}"),
        InlineKeyboardButton("❌ Huỷ", callback_data=f"back_det:{event_id}"),
    ]])


def _edit_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Xác nhận sửa", callback_data="ed_go"),
        InlineKeyboardButton("❌ Huỷ", callback_data="ed_no"),
    ]])


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
                  ("pending", "pending_edit", "pending_delete", "pending_sync", "edit_mode"))
        for k in ("pending", "pending_edit", "pending_delete",
                  "pending_sync", "edit_mode", "occurrences"):
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

    # 4) Default: create flow
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
    preview = formatter.format_confirm_preview(cmd)
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Xác nhận tạo", callback_data="cr_confirm"),
        InlineKeyboardButton("❌ Huỷ", callback_data="cr_cancel"),
    ]])
    await update.message.reply_text(preview, reply_markup=keyboard,
                                     parse_mode=ParseMode.MARKDOWN)


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
    row = _resolve_target(ctx, target_id)
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
    target_id: int | None,
) -> None:
    """Resolve target event (id from #N or latest), then preview + confirm."""
    row = _resolve_target(ctx, target_id)
    if row is None:
        await update.message.reply_text(
            "⚠️ Không tìm thấy lịch để sửa. "
            "Dùng `#id` để chỉ định (VD `sửa giờ 15h #3`) hoặc gõ /list."
        )
        return

    if field == "delete":
        ctx.chat_data["pending_delete"] = {"event_id": row.id}
        detail = formatter.format_event_detail(row)
        note = ""
        if row.recurring:
            note = (
                "\n\n⚠️ Đây là lịch lặp. Xoá tại đây sẽ huỷ *toàn bộ series*. "
                "Muốn huỷ 1 buổi thôi thì /list → bấm lịch → 🗑 → *Chỉ 1 buổi*."
            )
        await update.message.reply_text(
            f"🗑 Xoá lịch sau?\n\n{detail}{note}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Xoá thật", callback_data=f"del_all:{row.id}"),
                InlineKeyboardButton("❌ Huỷ", callback_data="del_no"),
            ]]),
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
        return

    try:
        new_value, display = _parse_edit(row, field, value)
    except ParseError as e:
        await update.message.reply_text(f"⚠️ {e}")
        return

    ctx.chat_data["pending_edit"] = {
        "event_id": row.id,
        "field": field,
        "new_value": new_value,
        "display": display,
    }
    preview = formatter.format_edit_preview(row, field, display)
    await update.message.reply_text(
        preview,
        reply_markup=_edit_confirm_keyboard(),
        parse_mode=ParseMode.MARKDOWN,
    )


def _resolve_target(ctx: ContextTypes.DEFAULT_TYPE, target_id: int | None) -> db.EventRow | None:
    if target_id is not None:
        row = db.get_event(target_id)
        if row and row.status == "active":
            return row
        return None
    # No explicit #id → use last-created in this chat, fallback to DB latest
    last_id = ctx.chat_data.get("last_created_id")
    if last_id:
        row = db.get_event(last_id)
        if row and row.status == "active":
            return row
    return db.latest_created()


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
            rows = db.list_recent(limit=10)
            await query.edit_message_text(
                formatter.format_list(rows),
                reply_markup=_list_keyboard(rows),
                parse_mode=ParseMode.MARKDOWN,
            )
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

        if data == "ed_go":
            await _do_edit(update, ctx)
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
                    f"{row.recurring['count']} buổi)?\n"
                    f"Khách sẽ nhận email huỷ tương ứng.",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("❌ Toàn bộ series",
                                              callback_data=f"del_all:{event_id}")],
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
            event_id = int(data.split(":", 1)[1])
            ctx.chat_data.pop("pending_delete", None)
            await _do_delete(query, event_id)
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

        if data.startswith("del_occ_go:"):
            _, eid, idx = data.split(":", 2)
            event_id, i = int(eid), int(idx)
            occs = ctx.chat_data.get("occurrences", {}).get(event_id)
            if not occs or i >= len(occs):
                await query.edit_message_text("⚠️ Danh sách đã cũ. Gõ /list làm lại.")
                return
            await _do_delete_occurrence(query, event_id, occs[i])
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
async def _do_edit(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
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
    await query.edit_message_text("⏳ Đang apply thay đổi lên Zoom + Calendar…")
    try:
        if occ_idx is not None:
            occs = ctx.chat_data.get("occurrences", {}).get(event_id, [])
            if occ_idx >= len(occs):
                raise RuntimeError("Danh sách buổi đã cũ, gõ /list làm lại.")
            _apply_occurrence_edit(row, occs[occ_idx], field, new_value)
        else:
            _apply_edit(row, field, new_value)
        ctx.chat_data.pop("pending_edit", None)
        updated = db.get_event(event_id)
        await query.edit_message_text(
            "✅ Đã cập nhật.\n\n" + formatter.format_event_detail(updated),
            reply_markup=_detail_keyboard(event_id),
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
    except Exception as e:
        log.exception("Failed to apply edit")
        await query.edit_message_text(
            f"❌ Lỗi apply: `{e}`", parse_mode=ParseMode.MARKDOWN
        )


def _apply_edit(row: db.EventRow, field: str, new_value) -> None:
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
async def _do_delete(query, event_id: int) -> None:
    row = db.get_event(event_id)
    if row is None or row.status != "active":
        await query.edit_message_text("⚠️ Lịch này không còn.")
        return
    await query.edit_message_text("⏳ Đang xoá Zoom + Calendar…")
    try:
        _zoom.delete_meeting(row.zoom_meeting_id)
    except Exception:
        log.exception("Zoom delete failed (soft-continuing)")
    try:
        _get_calendar().delete_event(row.calendar_event_id)
    except Exception:
        log.exception("Calendar delete failed (soft-continuing)")
    db.mark_deleted(event_id)
    await query.edit_message_text(
        f"🗑 Đã xoá lịch *{row.topic}* (id={event_id}). Khách đã nhận email huỷ.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("↩️ Quay lại list", callback_data="back_list")]]
        ),
        parse_mode=ParseMode.MARKDOWN,
    )


# ── Single-occurrence apply (recurring series) ────────────────────────────────
def _apply_occurrence_edit(
    row: db.EventRow, occ: dict, field: str, new_value
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

    prefix = "del_occ_go" if action == "xoá" else "ed_occ_sel"
    text = formatter.format_occurrence_list(row, occs, action)
    await query.edit_message_text(
        text,
        reply_markup=_occurrence_keyboard(event_id, occs, prefix),
        parse_mode=ParseMode.MARKDOWN,
    )


async def _do_delete_occurrence(query, event_id: int, occ: dict) -> None:
    row = db.get_event(event_id)
    if not row:
        await query.edit_message_text("⚠️ Lịch không tồn tại.")
        return
    await query.edit_message_text("⏳ Đang huỷ 1 buổi trên Zoom + Calendar…")
    try:
        if occ["zoom_occ_id"]:
            try:
                _zoom.delete_occurrence(row.zoom_meeting_id, occ["zoom_occ_id"])
            except Exception:
                log.exception("Zoom occurrence delete failed")
        if occ["cal_instance_id"]:
            try:
                _get_calendar().cancel_instance(occ["cal_instance_id"])
            except Exception:
                log.exception("Calendar instance cancel failed")
        db.add_cancelled_occurrence(event_id, occ["start_local"])
        date_str = formatter.format_occurrence_date(occ)
        await query.edit_message_text(
            f"✅ Đã huỷ buổi *{date_str}* trong lịch lặp *{row.topic}*. "
            f"Các buổi còn lại giữ nguyên.",
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
