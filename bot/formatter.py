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


def format_personal_calendar_description(*, cmd: ParsedCommand, meet_link: str) -> str:
    """Calendar description cho lịch HY cá nhân (không ghi tên JA, không có Zoom)."""
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
    link_line = f"\n🔗 Google Meet: {meet_link}" if meet_link else ""
    return (
        f"📅 Thời gian: {time_line}\n"
        f"⏱️ Thời lượng: {duration_line}\n"
        f"🎯 Nội dung: {agenda}"
        f"{link_line}"
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
    if cmd.is_personal:
        header = (
            "🔒 *Lịch HY cá nhân* — em hiểu như sau:\n"
            "_Google Meet auto-gen · visibility: private · không Zoom_\n\n"
        )
    else:
        header = "📋 Em hiểu lệnh như sau, chị xác nhận giúp em:\n\n"

    problems_block = ""
    problems = getattr(cmd, "attendees_problems", None) or []
    if problems:
        lines = ["", "⚠️ *Em không hiểu vài người trong dòng Khách:*"]
        for p in problems:
            lines.append(f"  • {p}")
        lines.append(
            "_Chị sửa lại bằng email đầy đủ, hoặc gõ `/members add <email> <tên>` "
            "rồi gửi lại lệnh._"
        )
        problems_block = "\n".join(lines)

    return (
        header
        + f"🏷 *Tên:* {cmd.topic}\n"
        + f"📅 *Thời gian:* {time_line} ({cmd.duration_min} phút)\n"
        + f"{recur_line}"
        + f"🎯 *Nội dung:* {cmd.agenda or '(không)'}\n"
        + f"👥 *Khách mời* ({len(cmd.attendees)} người):\n{attendees}"
        + problems_block
    )


def format_personal_success_reply(
    *,
    cmd: ParsedCommand,
    meet_link: str,
    calendar_event_link: str,
) -> str:
    """Success reply cho lịch HY cá nhân (Meet, private, không Zoom)."""
    if cmd.recurring:
        from datetime import timedelta as td
        occ_lines = []
        for i in range(cmd.recurring["count"]):
            d = cmd.start + td(weeks=i)
            occ_lines.append(f"  • {d.day}/{d.month}/{d.year}")
        time_summary = (
            f"🔁 Recurring {cmd.recurring['count']} buổi "
            f"({_WEEKDAY_BYDAY_TO_VI[cmd.recurring['byday']]} hàng tuần):\n"
            + "\n".join(occ_lines) + "\n"
        )
    else:
        time_summary = (
            f"📅 {_fmt_date(cmd.start)}, "
            f"{_fmt_time_range(cmd.start, cmd.duration_min)}\n"
        )
    attendee_block = (
        "\n".join(f"  • {e}" for e in cmd.attendees)
        if cmd.attendees else "  (chỉ mình chị)"
    )
    meet_line = (
        f"🔗 *Google Meet:*\n{meet_link}\n\n" if meet_link
        else "_(Meet link sẽ xuất hiện trên Calendar sau vài giây.)_\n\n"
    )
    return (
        f"🔒 Đã tạo lịch HY: *{cmd.topic}*\n"
        f"_Visibility: private · không Zoom · không JA branding_\n\n"
        f"{time_summary}"
        f"⏱ {cmd.duration_min} phút/buổi\n\n"
        f"{meet_line}"
        f"👥 *Khách mời:*\n{attendee_block}\n\n"
        f"🗓 [Mở Calendar event]({calendar_event_link})"
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
        is_personal=(row.provider == "meet"),
    )


def format_event_summary(row: EventRow) -> str:
    """One-line label for the /list buttons."""
    d = row.start_dt
    tag = "🔁" if row.recurring else "📅"
    hy = "🔒 " if row.provider == "meet" else ""
    date = f"{d.day}/{d.month} {d.hour:02d}:{d.minute:02d}"
    topic = row.topic if len(row.topic) <= 32 else row.topic[:30] + "…"
    return f"{tag} {date} · {hy}{topic}"


def format_list(
    rows: list[EventRow],
    *,
    total: int | None = None,
    page: int = 1,
    page_size: int = 10,
    query_desc: str = "",
    externals: list | None = None,
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

    externals = externals or []
    if not rows and not externals:
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
    for i, r in enumerate(rows, 1):
        lines.append(f"{i}. {format_event_summary(r)} · id=`{r.id}`")

    if externals:
        lines.append("")
        lines.append(f"📅 *{len(externals)} lịch từ Calendar* _(không do bot tạo)_:")
        for i, occ in enumerate(externals, 1):
            d = occ.start_dt
            date_str = f"{d.day}/{d.month} {d.hour:02d}:{d.minute:02d}"
            topic = occ.topic if len(occ.topic) <= 36 else occ.topic[:34] + "…"
            lines.append(f"E{i}. {date_str} · {topic}")
        lines.append("_Bấm nút `E1`/`E2`… để xem và sửa/xoá lịch Calendar._")

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
    is_hy = row.provider == "meet"
    title_prefix = "🔒 " if is_hy else ""
    hy_tag = "\n_🔒 Lịch HY cá nhân · private · Meet_" if is_hy else ""
    if is_hy:
        link_line = (
            f"🔗 [Meet]({row.meet_join_url})  |  🗓 [Calendar]({row.calendar_event_link})"
            if row.meet_join_url
            else f"🗓 [Calendar]({row.calendar_event_link})"
        )
    else:
        link_line = (
            f"🔗 [Zoom]({row.zoom_join_url})  |  🗓 [Calendar]({row.calendar_event_link})"
        )
    return (
        f"🏷 *{title_prefix}{row.topic}* (id={row.id}){hy_tag}\n"
        f"{time_line}\n"
        f"⏱ {row.duration_min} phút\n"
        f"🎯 {row.agenda or '(không)'}\n"
        f"👥 Khách:\n{attendees}\n"
        f"{link_line}"
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


def format_external_detail(occ: dict) -> str:
    """Full detail view for an external Calendar event (not bot-created)."""
    d = datetime.fromisoformat(occ["occurrence_iso"])
    end = d + timedelta(minutes=occ["duration_min"])
    time_line = (
        f"📅 {_fmt_date(d)}, "
        f"{d.hour:02d}:{d.minute:02d} - {end.hour:02d}:{end.minute:02d}"
    )
    attendees = occ.get("attendees") or []
    att_line = (
        "\n".join(f"  • {e}" for e in attendees) if attendees else "  (không)"
    )
    link = occ.get("html_link") or ""
    link_line = f"\n🗓 [Mở Calendar]({link})" if link else ""
    recur_tag = (
        "\n🔁 _(1 buổi của lịch lặp — chỉnh chỉ ảnh hưởng buổi này)_"
        if occ.get("recurring_source_id") else ""
    )
    agenda = occ.get("agenda") or "(không)"
    return (
        f"🏷 *{occ['topic']}* _(từ Calendar — không do bot tạo)_\n"
        f"{time_line}\n"
        f"⏱ {occ['duration_min']} phút\n"
        f"🎯 {agenda}\n"
        f"👥 Khách:\n{att_line}"
        f"{link_line}"
        f"{recur_tag}"
    )


def format_external_edit_preview(occ: dict, field: str, new_display: str) -> str:
    label = _EDIT_LABELS.get(field, field)
    return (
        f"✏️ *Sửa {label}* — lịch Calendar *{occ['topic']}*:\n\n"
        f"{new_display}\n\n"
        f"Chị xác nhận?"
    )


def format_directory_panel(
    *,
    members_on_page: list,
    page: int,
    total_pages: int,
    selected_emails: set[str],
    base_emails: set[str],
    base_index: int,
    kind: str,
) -> str:
    """Render text panel cho member picker (Section 14.3 design doc).

    `base_index` = (page-1) * PAGE_SIZE — global index của member đầu tiên trong slice,
    để hiển thị đúng số thứ tự khớp với callback `dir_t:<idx>` (idx toàn cục).
    """
    kind_label = {
        "create": "thêm KHÁCH cho lịch sắp tạo",
        "edit_add": "thêm KHÁCH cho lịch đang sửa",
        "ext_add": "thêm KHÁCH cho lịch Calendar đang sửa",
    }.get(kind, "thêm KHÁCH")

    if not members_on_page and total_pages == 1:
        return (
            "📇 *Sổ thành viên công ty*\n\n"
            "📭 Sổ trống. Gõ `/members add <email> <tên>` để bắt đầu.\n"
            "_VD: `/members add lan@abc.com Chị Lan`_"
        )

    lines = [
        f"📇 *Sổ thành viên công ty* — bấm số để {kind_label}.",
        f"_✓ = đã có trong khách mời · ✅ = chị vừa chọn ở phiên này_",
        "",
    ]
    for i, m in enumerate(members_on_page):
        idx = base_index + i
        in_base = m.email in base_emails
        picked_now = m.email in selected_emails and not in_base
        if in_base:
            tag = "✓"
        elif picked_now:
            tag = "✅"
        else:
            tag = "·"
        title_part = f" · {m.title}" if m.title else ""
        lines.append(f"{idx + 1}. {tag} *{m.name}*{title_part} · `{m.email}`")
    lines.append("")
    new_picks = [e for e in selected_emails if e not in base_emails]
    if new_picks:
        lines.append(f"🛒 Đã chọn ({len(new_picks)}): " + ", ".join(new_picks))
    else:
        lines.append("🛒 Chưa chọn ai mới — bấm số để thêm.")
    if total_pages > 1:
        lines.append(f"_Trang {page}/{total_pages}_")
    return "\n".join(lines)


def format_conflict_warning(conflicts: list[tuple[EventRow, str]]) -> str:
    """Warning block appended to create/edit-time previews when overlaps exist."""
    if not conflicts:
        return ""
    lines = [
        "",
        f"⚠️ *Cảnh báo trùng lịch* — phát hiện {len(conflicts)} lịch đã có overlap:",
    ]
    for ev, occ_iso in conflicts:
        d = datetime.fromisoformat(occ_iso)
        end = d + timedelta(minutes=ev.duration_min)
        time_str = (
            f"{d.day}/{d.month}/{d.year} "
            f"{d.hour:02d}:{d.minute:02d}–{end.hour:02d}:{end.minute:02d}"
        )
        prefix = "🔁" if ev.recurring else "·"
        lines.append(f"  {prefix} id={ev.id} *{ev.topic}* lúc {time_str}")
    lines.append("_Chị vẫn có thể confirm nếu cố ý trùng._")
    return "\n".join(lines)
