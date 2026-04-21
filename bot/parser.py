"""Vietnamese natural-language parser for scheduling commands.

MVP: pure regex, handles the brief's structured format. Optimized for reliability
over flexibility — chị Yến can adapt her command format if edge cases don't parse.

Expected input (one-time):
    Tạo lịch "Tư vấn OKRs - Chị Lan":
    - Thời gian: 22/4/2026 14:00
    - Thời lượng: 30 phút
    - Nội dung: Tư vấn gói Coaching OKRs
    - Khách: lan@abc.com

Expected input (recurring):
    Tạo lịch "Mentor MBOs 42":
    - Thời gian: 8h30 sáng thứ 4 hàng tuần trong 12 tuần liên tiếp bắt đầu từ 20/5/2026
    - Thời lượng: 120 phút
    - Nội dung: Chương trình Mentor MBOs
    - Mời khách: nguyenthihaiyen@john.vn, a@b.vn
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, date


class ParseError(Exception):
    """Raised when a command cannot be parsed. Message is user-facing Vietnamese."""


@dataclass
class ParsedCommand:
    topic: str
    start: datetime  # naive local (Asia/Ho_Chi_Minh)
    duration_min: int
    agenda: str
    attendees: list[str]
    recurring: dict | None = None  # {"byday": "WE", "count": 12} or None
    raw_labels: dict[str, str] = field(default_factory=dict)


# Vietnamese weekday → RRULE BYDAY
_WEEKDAY = {
    "thứ 2": "MO", "thứ hai": "MO", "t2": "MO",
    "thứ 3": "TU", "thứ ba": "TU", "t3": "TU",
    "thứ 4": "WE", "thứ tư": "WE", "t4": "WE",
    "thứ 5": "TH", "thứ năm": "TH", "t5": "TH",
    "thứ 6": "FR", "thứ sáu": "FR", "t6": "FR",
    "thứ 7": "SA", "thứ bảy": "SA", "t7": "SA",
    "chủ nhật": "SU", "cn": "SU",
}

# Key labels users write after "- "
_LABEL_ALIASES = {
    "thời gian": "time",
    "thoi gian": "time",
    "thời lượng": "duration",
    "thoi luong": "duration",
    "nội dung": "agenda",
    "noi dung": "agenda",
    "khách": "attendees",
    "khach": "attendees",
    "mời khách": "attendees",
    "moi khach": "attendees",
    "email": "attendees",
}

_RE_TOPIC = re.compile(
    r'(?:t[aạ]o\s+l[iị]ch|l[iị]ch)\s*[:：]?\s*["\u201c\']?(?P<title>[^"\u201d\n]+?)["\u201d\']?\s*[:：]?\s*(?=\n)',
    re.IGNORECASE,
)
_RE_LABEL_LINE = re.compile(r'^\s*[-•*]\s*([^:：]+)\s*[:：]\s*(.+?)\s*$')
_RE_EMAIL = re.compile(r'\b[\w.+-]+@[\w-]+\.[\w.-]+\b')
_RE_TIME = re.compile(
    r'(\d{1,2})\s*(?:h|giờ|:)\s*(\d{0,2})\s*(sáng|chiều|tối|trưa|đêm|sang|chieu|toi|trua|dem)?',
    re.IGNORECASE,
)
_RE_DATE = re.compile(r'(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?')
_RE_DURATION = re.compile(
    r'(\d+)\s*(phút|phut|p|giờ|gio|h|tiếng|tieng)',
    re.IGNORECASE,
)
_RE_RECUR_COUNT = re.compile(
    r'(\d+)\s*(?:tuần|tuan|lần|lan|buổi|buoi)\s*(?:liên tiếp|lien tiep|liền|liên tục)?',
    re.IGNORECASE,
)


def parse_command(text: str) -> ParsedCommand:
    """Parse Vietnamese scheduling command. Raise ParseError if critical field missing."""
    topic = _parse_topic(text)
    labels = _extract_labels(text)

    time_str = labels.get("time")
    if not time_str:
        raise ParseError("Em chưa thấy dòng '- Thời gian: ...'. Chị kiểm tra lại giúp em.")

    start, recurring = _parse_time_and_recurrence(time_str)

    duration_min = _parse_duration(labels.get("duration", ""))
    if not duration_min:
        raise ParseError("Em chưa hiểu dòng '- Thời lượng'. Ví dụ: '30 phút' hoặc '2 tiếng'.")

    agenda = labels.get("agenda", "").strip()
    attendees = _parse_emails(labels.get("attendees", ""))

    return ParsedCommand(
        topic=topic,
        start=start,
        duration_min=duration_min,
        agenda=agenda,
        attendees=attendees,
        recurring=recurring,
        raw_labels=labels,
    )


def _parse_topic(text: str) -> str:
    m = _RE_TOPIC.search(text + "\n")  # append newline so regex lookahead matches on last line
    if not m:
        # Fallback: use first line after "tạo lịch"
        first = text.strip().splitlines()[0]
        raise ParseError(
            f'Em chưa parse được tên lịch. Chị dùng format: Tạo lịch "TÊN": ...'
            f' (dòng đầu chị ghi: {first!r})'
        )
    title = m.group("title").strip().strip('"\u201c\u201d\'')
    if not title:
        raise ParseError("Tên lịch trống. Chị điền tên giữa dấu ngoặc kép.")
    return title


def _extract_labels(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        m = _RE_LABEL_LINE.match(line)
        if not m:
            continue
        key_raw = m.group(1).strip().lower()
        val = m.group(2).strip()
        key = _LABEL_ALIASES.get(key_raw)
        if key:
            out[key] = val
    return out


def _parse_time_and_recurrence(s: str) -> tuple[datetime, dict | None]:
    """Return (start_datetime_naive_local, recurring_dict_or_None).

    Handles:
      - '22/4/2026 14:00'            → one-time
      - '14:00 22/4/2026'
      - '8h30 sáng thứ 4 hàng tuần trong 12 tuần liên tiếp bắt đầu từ 20/5/2026'
    """
    s_low = s.lower()

    # Extract time
    tm = _RE_TIME.search(s_low)
    if not tm:
        raise ParseError(f"Em không tìm được giờ trong: {s!r}. Ví dụ: '8h30', '14:00'.")
    hour = int(tm.group(1))
    minute = int(tm.group(2) or "0")
    period = tm.group(3) or ""
    if period in ("chiều", "chieu", "tối", "toi", "đêm", "dem") and hour < 12:
        hour += 12
    if period in ("sáng", "sang") and hour == 12:
        hour = 0

    # Extract date (dd/mm[/yyyy])
    dm = _RE_DATE.search(s)
    if not dm:
        raise ParseError(
            f"Em không tìm được ngày trong: {s!r}. Ví dụ: '22/4/2026' hoặc '20/5/2026'."
        )
    day = int(dm.group(1))
    month = int(dm.group(2))
    year_raw = dm.group(3)
    year = _resolve_year(year_raw, month, day)
    start = datetime(year, month, day, hour, minute)

    # Check recurring keywords
    recurring: dict | None = None
    if any(kw in s_low for kw in ("hàng tuần", "hang tuan", "mỗi tuần", "moi tuan",
                                   "liên tiếp", "lien tiep", "mỗi thứ", "moi thu")):
        # Find weekday (e.g. "thứ 4")
        byday = _find_weekday(s_low)
        # If not explicit, infer from start date
        if not byday:
            byday = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")[start.weekday()]
        # Count of occurrences
        rc = _RE_RECUR_COUNT.search(s_low)
        count = int(rc.group(1)) if rc else 0
        if not count:
            raise ParseError(
                "Recurring cần số buổi. Ví dụ: '12 tuần liên tiếp' hoặc '4 buổi'."
            )
        recurring = {"byday": byday, "count": count}

    return start, recurring


def _find_weekday(s_low: str) -> str | None:
    # longest match first
    for vi, code in sorted(_WEEKDAY.items(), key=lambda x: -len(x[0])):
        if vi in s_low:
            return code
    return None


def _resolve_year(year_raw: str | None, month: int, day: int) -> int:
    today = date.today()
    if year_raw:
        y = int(year_raw)
        return y + 2000 if y < 100 else y
    # Year omitted → next occurrence of that month/day
    candidate = date(today.year, month, day)
    return today.year if candidate >= today else today.year + 1


def _parse_duration(s: str) -> int:
    s_low = s.lower()
    m = _RE_DURATION.search(s_low)
    if not m:
        return 0
    n = int(m.group(1))
    unit = m.group(2).lower()
    if unit.startswith(("giờ", "gio", "tiếng", "tieng", "h")):
        return n * 60
    return n  # phút


def _parse_emails(s: str) -> list[str]:
    return list(dict.fromkeys(_RE_EMAIL.findall(s)))  # dedupe, preserve order


# ── Edit-value parsers (single-field, called when user is in edit mode) ────────
def parse_edit_time(s: str, *, base: datetime) -> datetime:
    """Parse a time-and/or-date string. Missing parts fall back to `base`.

    Accepted examples:
      - "15h30 25/4/2026"   → full
      - "15h30"             → same date as base, new time
      - "25/4" / "25/4/2026"→ same time as base, new date
    """
    s_low = s.lower()
    tm = _RE_TIME.search(s_low)
    dm = _RE_DATE.search(s)
    if not tm and not dm:
        raise ParseError("Em chưa hiểu giờ/ngày mới. VD: `15h30 25/4/2026` hoặc `15h30`.")
    if tm:
        hour = int(tm.group(1))
        minute = int(tm.group(2) or "0")
        period = tm.group(3) or ""
        if period in ("chiều", "chieu", "tối", "toi", "đêm", "dem") and hour < 12:
            hour += 12
        if period in ("sáng", "sang") and hour == 12:
            hour = 0
    else:
        hour, minute = base.hour, base.minute
    if dm:
        day = int(dm.group(1))
        month = int(dm.group(2))
        year_raw = dm.group(3)
        year = _resolve_year(year_raw, month, day)
    else:
        day, month, year = base.day, base.month, base.year
    return datetime(year, month, day, hour, minute)


def parse_edit_duration(s: str) -> int:
    n = _parse_duration(s)
    if not n:
        raise ParseError("Em chưa hiểu thời lượng. VD: `30 phút`, `1.5 tiếng`, `90`.")
    return n


def parse_edit_emails(s: str) -> list[str]:
    emails = _parse_emails(s)
    if not emails:
        raise ParseError("Em không tìm thấy email nào. Nhắn dạng `a@x.vn, b@y.vn`.")
    return emails


def parse_edit_plain(s: str, *, label: str) -> str:
    v = s.strip().strip('"\u201c\u201d\'')
    if not v:
        raise ParseError(f"{label} trống. Chị nhắn nội dung mới giúp em.")
    return v


# ── Quick-edit parser (Phương án A hybrid) ─────────────────────────────────────
# Match verbs like:
#   "sửa giờ 15h30", "đổi thời lượng 45 phút", "thêm khách a@x.vn",
#   "bỏ khách a@x.vn", "xoá lịch", "huỷ lịch", optionally with "#id" target.
_QUICK_KEYWORDS = {
    ("sửa", "giờ"): "time",
    ("đổi", "giờ"): "time",
    ("sua", "gio"): "time",
    ("doi", "gio"): "time",
    ("sửa", "ngày"): "time",
    ("đổi", "ngày"): "time",
    ("sửa", "thời lượng"): "dur",
    ("đổi", "thời lượng"): "dur",
    ("sua", "thoi luong"): "dur",
    ("doi", "thoi luong"): "dur",
    ("sửa", "tên"): "topic",
    ("đổi", "tên"): "topic",
    ("sửa", "nội dung"): "ag",
    ("đổi", "nội dung"): "ag",
    ("sua", "noi dung"): "ag",
    ("doi", "noi dung"): "ag",
    ("thêm", "khách"): "att_add",
    ("them", "khach"): "att_add",
    ("bỏ", "khách"): "att_rm",
    ("bo", "khach"): "att_rm",
    ("xoá", "lịch"): "delete",
    ("xóa", "lịch"): "delete",
    ("huỷ", "lịch"): "delete",
    ("huy", "lich"): "delete",
    ("xoa", "lich"): "delete",
}

_RE_TARGET_ID = re.compile(r"#(\d+)")


def parse_quick_edit(text: str) -> tuple[str, str, int | None] | None:
    """Try to match a quick-edit command.

    Returns (field, value_str, target_id_or_None) or None if text isn't one.
    `field` is one of: time, dur, topic, ag, att_add, att_rm, delete.
    """
    raw = text.strip()
    if not raw:
        return None

    # Extract optional #<id>
    target_id: int | None = None
    m_id = _RE_TARGET_ID.search(raw)
    if m_id:
        target_id = int(m_id.group(1))
        raw = (raw[: m_id.start()] + raw[m_id.end():]).strip()

    low = raw.lower()
    # Find the longest matching (verb, object) prefix
    matched_field = None
    matched_len = 0
    for (verb, obj), field in _QUICK_KEYWORDS.items():
        pref = f"{verb} {obj}"
        if low.startswith(pref):
            # Ensure word boundary (next char is space, end, or punctuation)
            end = len(pref)
            if end == len(low) or low[end] in " :,\t\n":
                if end > matched_len:
                    matched_field = field
                    matched_len = end
    if matched_field is None:
        return None

    value = raw[matched_len:].lstrip(" :,\t").strip()
    # "xoá lịch" can take no value
    if matched_field == "delete":
        return ("delete", "", target_id)
    if not value:
        raise ParseError(
            "Em thấy lệnh sửa nhưng chưa có giá trị mới. "
            "VD: `sửa giờ 15h30`, `thêm khách a@x.vn`."
        )
    return (matched_field, value, target_id)
