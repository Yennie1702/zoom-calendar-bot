"""String formatters: Calendar description, Telegram replies, confirmation preview."""
from __future__ import annotations

from datetime import datetime, timedelta

from bot import config
from bot.db import EventRow
from bot.parser import ParsedCommand

_WEEKDAY_VI = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]
_WEEKDAY_BYDAY_TO_VI = {
    "MO": "Thứ 2", "TU": "Thứ 3", "WE": "Thứ 4", "TH": "Thứ 5",
    "FR": "Thứ 6", "SA": "Thứ 7", "SU": "Chủ nhật",
}


def _fmt_date(d: datetime) -> str:
    return f"{_WEEKDAY_VI[d.weekday()]}, {d.day}/{d.month}/{d.year}"


def _fmt_time_range(start: datetime, duration_min: int) -> str:
    end = start + timedelta(minutes=duration_min)
    return f"{start.hour:02d}:{start.minute:02d} - {end.hour:02d}:{end.minute:02d}"


def format_calendar_description(
    *,
    cmd: ParsedCommand,
    zoom_join_url: str,
    zoom_meeting_id: int,
    zoom_passcode: str,
) -> str:
    """Calendar event description — matches chị Yến's approved template (2026-04-21)."""
    if cmd.recurring:
        weekday_vi = _WEEKDAY_BYDAY_TO_VI[cmd.recurring["byday"]]
        end_date = cmd.start + timedelta(weeks=cmd.recurring["count"] - 1)
        time_line = (
            f"{_fmt_time_range(cmd.start, cmd.duration_min)}, "
            f"{weekday_vi} hàng tuần ({cmd.recurring['count']} buổi: "
            f"{cmd.start.day}/{cmd.start.month}/{cmd.start.year} → "
            f"{end_date.day}/{end_date.month}/{end_date.year})"
        )
        duration_line = f"{cmd.duration_min} phút/buổi"
    else:
        time_line = f"{_fmt_time_range(cmd.start, cmd.duration_min)}, {_fmt_date(cmd.start)}"
        duration_line = f"{cmd.duration_min} phút"

    agenda = cmd.agenda or cmd.topic

    return (
        "Kính gửi anh/chị,\n\n"
        f"{config.CONTACT_NAME} (John Academy) xin xác nhận lịch:\n"
        "─────────────────────────────\n"
        f"📅 Thời gian: {time_line}\n"
        f"⏱️ Thời lượng: {duration_line}\n"
        f"🎯 Nội dung: {agenda}\n"
        f"👤 Người tạo: {config.CONTACT_NAME} - {config.CONTACT_TITLE}\n"
        "─────────────────────────────\n\n"
        f"🔗 LINK ZOOM: {zoom_join_url}\n"
        f"🆔 Meeting ID: {zoom_meeting_id}\n"
        f"🔑 Passcode: {zoom_passcode}\n\n"
        "Anh chị tham gia zoom đúng giờ nhé.\n\n"
        "Trân trọng,\n"
        f"{config.CONTACT_NAME} | John Academy"
    )


def format_confirm_preview(cmd: ParsedCommand) -> str:
    """Preview message sent to chị Yến before creating the meeting."""
    if cmd.recurring:
        weekday_vi = _WEEKDAY_BYDAY_TO_VI[cmd.recurring["byday"]]
        end_date = cmd.start + timedelta(weeks=cmd.recurring["count"] - 1)
        time_line = (
            f"{weekday_vi}, bắt đầu {cmd.start.day}/{cmd.start.month}/{cmd.start.year}, "
            f"{_fmt_time_range(cmd.start, cmd.duration_min)}"
        )
        recur_line = (
            f"🔁 Lặp lại: {cmd.recurring['count']} tuần liên tiếp "
            f"(đến {end_date.day}/{end_date.month}/{end_date.year})\n"
        )
    else:
        time_line = f"{_fmt_date(cmd.start)}, {_fmt_time_range(cmd.start, cmd.duration_min)}"
        recur_line = ""

    attendees = (
        "\n".join(f"  • {e}" for e in cmd.attendees)
        if cmd.attendees
        else "  (chưa có khách — chỉ tạo lịch trên calendar chị)"
    )

    return (
        "📋 Em hiểu lệnh như sau, chị xác nhận giúp em:\n\n"
        f"🏷 *Tên:* {cmd.topic}\n"
        f"📅 *Thời gian:* {time_line} ({cmd.duration_min} phút)\n"
        f"{recur_line}"
        f"🎯 *Nội dung:* {cmd.agenda or '(không)'}\n"
        f"👥 *Khách mời* ({len(cmd.attendees)} người):\n{attendees}"
    )


def format_success_reply(
    *,
    cmd: ParsedCommand,
    zoom_join_url: str,
    zoom_meeting_id: int,
    zoom_passcode: str,
    calendar_event_link: str,
) -> str:
    """Final reply after successful creation."""
    if cmd.recurring:
        occ_lines = []
        from datetime import timedelta as td
        for i in range(cmd.recurring["count"]):
            d = cmd.start + td(weeks=i)
            occ_lines.append(f"  • {d.day}/{d.month}/{d.year}")
        occ_block = "\n".join(occ_lines)
        time_summary = (
            f"🔁 Recurring {cmd.recurring['count']} buổi "
            f"({_WEEKDAY_BYDAY_TO_VI[cmd.recurring['byday']]} hàng tuần):\n{occ_block}\n"
        )
    else:
        time_summary = f"📅 {_fmt_date(cmd.start)}, {_fmt_time_range(cmd.start, cmd.duration_min)}\n"

    attendee_block = (
        "\n".join(f"  • {e}" for e in cmd.attendees) if cmd.attendees else "  (không)"
    )

    return (
        f"✅ Đã tạo xong: *{cmd.topic}*\n\n"
        f"{time_summary}"
        f"⏱ {cmd.duration_min} phút/buổi\n\n"
        f"🔗 *Link Zoom:*\n{zoom_join_url}\n"
        f"🆔 Meeting ID: `{zoom_meeting_id}`\n"
        f"🔑 Passcode: `{zoom_passcode}`\n\n"
        f"📧 *Đã mời khách:*\n{attendee_block}\n\n"
        f"🗓 [Mở Calendar event]({calendar_event_link})"
    )


# ── /list + edit/delete helpers ────────────────────────────────────────────────
def event_to_parsed(row: EventRow) -> ParsedCommand:
    """Convert a DB row back to a ParsedCommand so format_* can be reused."""
    return ParsedCommand(
        topic=row.topic,
        start=row.start_dt,
        duration_min=row.duration_min,
        agenda=row.agenda,
        attendees=list(row.attendees),
        recurring=row.recurring,
    )


def format_event_summary(row: EventRow) -> str:
    """One-line label for the /list buttons."""
    d = row.start_dt
    tag = "🔁" if row.recurring else "📅"
    date = f"{d.day}/{d.month} {d.hour:02d}:{d.minute:02d}"
    topic = row.topic if len(row.topic) <= 32 else row.topic[:30] + "…"
    return f"{tag} {date} · {topic}"


def format_list(
    rows: list[EventRow],
    *,
    total: int | None = None,
    page: int = 1,
    page_size: int = 10,
    query_desc: str = "",
) -> str:
    """Render /list output.

    Keeps the legacy "10 lịch gần nhất" header when called without extra args so
    older callers and the back_list callback display identically. When pagination
    metadata is supplied, header shows page + filter summary.
    """
    if total is None and page == 1 and page_size == 10 and not query_desc:
        if not rows:
            return "📭 Chưa có lịch nào trong DB."
        lines = ["📋 *10 lịch gần nhất* (chọn số để xem/sửa):\n"]
        for i, r in enumerate(rows, 1):
            lines.append(f"{i}. {format_event_summary(r)}")
        return "\n".join(lines)

    if not rows:
        base = "📭 Không có lịch nào khớp."
        if query_desc:
            base += f"\n🔍 {query_desc}"
        return base

    total_pages = max(1, (total + page_size - 1) // page_size) if total else page
    header = f"📋 *Lịch* (trang {page}/{total_pages}"
    if total is not None:
        header += f", {total} lịch"
    header += ") — chọn số để xem/sửa"
    if query_desc:
        header += f"\n🔍 {query_desc}"
    header += ":\n"

    lines = [header]
    start_idx = (page - 1) * page_size
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {format_event_summary(r)} · id=`{r.id}`")
    return "\n".join(lines)


def format_candidate_list(rows: list[EventRow], action_label: str) -> str:
    """List header for disambiguation when a natural-target command matches >1 lịch."""
    header = (
        f"🔎 Tìm thấy *{len(rows)} lịch* khớp điều kiện. "
        f"Chị chọn lịch cần {action_label}:\n"
    )
    lines = [header]
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {format_event_summary(r)} · id=`{r.id}`")
    return "\n".join(lines)


def format_event_detail(row: EventRow) -> str:
    """Full detail view after selecting from /list."""
    cmd = event_to_parsed(row)
    d = row.start_dt
    if row.recurring:
        weekday_vi = _WEEKDAY_BYDAY_TO_VI[row.recurring["byday"]]
        end_date = d + timedelta(weeks=row.recurring["count"] - 1)
        total = row.recurring["count"]
        cancelled_n = len(row.cancelled_occurrences)
        active_n = total - cancelled_n
        count_suffix = (
            f"{total} buổi" if cancelled_n == 0
            else f"{active_n}/{total} buổi (đã huỷ {cancelled_n})"
        )
        time_line = (
            f"🔁 {weekday_vi} hàng tuần, bắt đầu {d.day}/{d.month}/{d.year} "
            f"{_fmt_time_range(d, row.duration_min)} "
            f"({count_suffix} → {end_date.day}/{end_date.month}/{end_date.year})"
        )
    else:
        time_line = f"📅 {_fmt_date(d)}, {_fmt_time_range(d, row.duration_min)}"

    attendees = (
        "\n".join(f"  • {e}" for e in row.attendees) if row.attendees else "  (không)"
    )
    status_tag = "" if row.status == "active" else f"\n⚠️ *Status:* {row.status}"
    return (
        f"🏷 *{row.topic}* (id={row.id})\n"
        f"{time_line}\n"
        f"⏱ {row.duration_min} phút\n"
        f"🎯 {row.agenda or '(không)'}\n"
        f"👥 Khách:\n{attendees}\n"
        f"🔗 [Zoom]({row.zoom_join_url})  |  🗓 [Calendar]({row.calendar_event_link})"
        f"{status_tag}"
    )


_EDIT_LABELS = {
    "time": "giờ/ngày",
    "dur": "thời lượng",
    "att_add": "thêm khách",
    "att_rm": "bỏ khách",
    "topic": "tên lịch",
    "ag": "nội dung",
}


def edit_prompt(field: str) -> str:
    prompts = {
        "time": "Nhắn giờ/ngày mới (VD: `15h30 25/4/2026`, `15h30`, `25/4`):",
        "dur": "Nhắn thời lượng mới (VD: `45 phút`, `1 tiếng`):",
        "att_add": "Nhắn email cần THÊM (VD: `a@x.vn, b@y.vn`):",
        "att_rm": "Nhắn email cần BỎ:",
        "topic": "Nhắn tên lịch mới:",
        "ag": "Nhắn nội dung (agenda) mới:",
    }
    return prompts.get(field, "Nhắn giá trị mới:")


def format_edit_preview(row: EventRow, field: str, new_display: str) -> str:
    label = _EDIT_LABELS.get(field, field)
    return (
        f"✏️ *Sửa {label}* cho lịch id={row.id} ({row.topic}):\n\n"
        f"{new_display}\n\n"
        f"Chị xác nhận?"
    )


def format_occurrence_preview(
    row: EventRow, occ: dict, field: str, new_display: str
) -> str:
    label = _EDIT_LABELS.get(field, field)
    d = datetime.fromisoformat(occ["start_local"])
    date_str = f"{_WEEKDAY_VI[d.weekday()]} {d.day}/{d.month}/{d.year} {d.hour:02d}:{d.minute:02d}"
    return (
        f"✏️ *Sửa {label}* cho 1 buổi riêng của lịch lặp:\n"
        f"📅 Buổi: {date_str}\n"
        f"🏷 Series: {row.topic} (id={row.id})\n\n"
        f"{new_display}\n\n"
        f"Chị xác nhận? (Chỉ buổi này đổi, các buổi khác giữ nguyên.)"
    )


def format_occurrence_list(
    row: EventRow, occurrences: list[dict], action: str
) -> str:
    """action: 'xoá' or 'sửa' — used in header text."""
    header = f"📅 *{row.topic}* — chọn buổi cần {action}:\n\n"
    lines = []
    for i, occ in enumerate(occurrences, 1):
        d = datetime.fromisoformat(occ["start_local"])
        tag = "❌ đã huỷ" if occ.get("cancelled") else "·"
        date_str = f"{_WEEKDAY_VI[d.weekday()]} {d.day}/{d.month}/{d.year} {d.hour:02d}:{d.minute:02d}"
        lines.append(f"{i}. {tag} {date_str}")
    return header + "\n".join(lines)


def format_occurrence_date(occ: dict) -> str:
    d = datetime.fromisoformat(occ["start_local"])
    return f"{_WEEKDAY_VI[d.weekday()]} {d.day}/{d.month} {d.hour:02d}:{d.minute:02d}"
