"""Vietnamese natural-language parser for scheduling commands.

Also hosts the clone parser: `tạo lịch giống <target> [nhưng <overrides>]`.

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
from datetime import datetime, date, timedelta


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
    is_personal: bool = False  # True khi keyword mở đầu là "HY" (lịch cá nhân)


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
# Lịch cá nhân "HY" — match HY as standalone first word, then title
_RE_TOPIC_HY = re.compile(
    r'^\s*HY\b\s*[:：]?\s*["\u201c\']?(?P<title>[^"\u201d\n]+?)["\u201d\']?\s*[:：]?\s*(?=\n)',
    re.IGNORECASE,
)
_RE_HY_PREFIX = re.compile(r'^\s*HY\b', re.IGNORECASE)


def is_personal_prefix(text: str) -> bool:
    """True when the command begins with the 'HY' keyword (lịch cá nhân)."""
    return bool(_RE_HY_PREFIX.match(text))
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
    """Parse Vietnamese scheduling command. Raise ParseError if critical field missing.

    Supports two openings:
      - "Tạo lịch ..." → standard work flow (Zoom + Calendar invite to khách)
      - "HY ..."       → lịch cá nhân (Meet auto-link + visibility=private, no Zoom)
    """
    is_personal = is_personal_prefix(text)
    topic = _parse_topic(text, is_personal=is_personal)
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
        is_personal=is_personal,
    )


def _parse_topic(text: str, *, is_personal: bool = False) -> str:
    pattern = _RE_TOPIC_HY if is_personal else _RE_TOPIC
    m = pattern.search(text + "\n")  # append newline so lookahead matches last line
    if not m:
        first = text.strip().splitlines()[0]
        fmt_hint = (
            'HY "TÊN":\\n- Thời gian: ...' if is_personal
            else 'Tạo lịch "TÊN":\\n- Thời gian: ...'
        )
        raise ParseError(
            f"Em chưa parse được tên lịch. Chị dùng format: {fmt_hint}"
            f" (dòng đầu chị ghi: {first!r})"
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
# Quoted topic: "..." or '...' or “...”
_RE_TARGET_TOPIC = re.compile(r'["\u201c\']([^"\u201d\']+)["\u201d\']')
# "ngày 25/4" or "ngay 25/4/2026"
_RE_TARGET_DATE = re.compile(
    r"(?:ngày|ngay)\s+(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?",
    re.IGNORECASE,
)
# "khách <email>" or "khach <email>"
_RE_TARGET_ATTENDEE = re.compile(
    r"(?:khách|khach)\s+([\w.+-]+@[\w-]+\.[\w.-]+)",
    re.IGNORECASE,
)


@dataclass
class TargetSpec:
    """How to find the event a quick-edit / action refers to."""
    id: int | None = None
    topic: str | None = None
    attendee: str | None = None
    date: str | None = None  # ISO 'YYYY-MM-DD'

    @property
    def is_natural(self) -> bool:
        return any([self.topic, self.attendee, self.date])

    @property
    def is_empty(self) -> bool:
        return not (self.id or self.is_natural)


def parse_quick_edit(text: str) -> tuple[str, str, TargetSpec] | None:
    """Try to match a quick-edit command.

    Returns (field, value_str, target_spec) or None if text isn't one.
    `field` is one of: time, dur, topic, ag, att_add, att_rm, delete.

    Target can be:
      - empty    → use latest-created in this chat
      - id       → `#42`
      - natural  → quoted "topic" + optional `ngày DD/MM` + optional `khách x@y`
    """
    raw = text.strip()
    if not raw:
        return None

    # 1) Strip #<id> first — it never conflicts with verbs
    target = TargetSpec()
    m_id = _RE_TARGET_ID.search(raw)
    if m_id:
        target.id = int(m_id.group(1))
        raw = (raw[: m_id.start()] + raw[m_id.end():]).strip()

    # 2) Find verb (longest prefix) before pulling other target specifiers —
    #    otherwise "thêm khách a@x.vn" would eat `khách a@x.vn` as a target.
    low = raw.lower()
    matched_field = None
    matched_len = 0
    for (verb, obj), fld in _QUICK_KEYWORDS.items():
        pref = f"{verb} {obj}"
        if low.startswith(pref):
            end = len(pref)
            if end == len(low) or low[end] in " :,\t\n":
                if end > matched_len:
                    matched_field = fld
                    matched_len = end
    if matched_field is None:
        return None

    rest = raw[matched_len:].lstrip(" :,\t").strip()

    # 3) From the rest, extract natural target specifiers (date, attendee-after-value, topic)
    rest, natural = _extract_natural_target(rest)
    target.topic = natural.topic or target.topic
    target.attendee = natural.attendee or target.attendee
    target.date = natural.date or target.date

    if matched_field == "delete":
        # "xoá lịch" may have no value left
        return ("delete", "", target)
    if not rest:
        raise ParseError(
            "Em thấy lệnh sửa nhưng chưa có giá trị mới. "
            "VD: `sửa giờ 15h30`, `thêm khách a@x.vn`."
        )
    return (matched_field, rest, target)


def _extract_natural_target(raw: str) -> tuple[str, TargetSpec]:
    """Pull topic / ngày / khách specifiers out of `raw` — id handled separately."""
    spec = TargetSpec()

    m = _RE_TARGET_DATE.search(raw)
    if m:
        day = int(m.group(1))
        month = int(m.group(2))
        year = _resolve_year(m.group(3), month, day)
        spec.date = f"{year:04d}-{month:02d}-{day:02d}"
        raw = raw[: m.start()] + raw[m.end():]

    m = _RE_TARGET_ATTENDEE.search(raw)
    if m:
        spec.attendee = m.group(1).lower()
        raw = raw[: m.start()] + raw[m.end():]

    m = _RE_TARGET_TOPIC.search(raw)
    if m:
        spec.topic = m.group(1).strip()
        raw = raw[: m.start()] + raw[m.end():]

    return raw.strip(), spec


# ── /list arg parser ──────────────────────────────────────────────────────────
@dataclass
class ListQuery:
    """Filter + pagination spec for /list."""
    topic: str | None = None
    attendee: str | None = None
    date_from: str | None = None  # ISO
    date_to: str | None = None    # ISO inclusive
    page: int = 1
    page_size: int = 10

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def has_filter(self) -> bool:
        return any([self.topic, self.attendee, self.date_from, self.date_to])

    def describe_vi(self) -> str:
        """Short Vietnamese description of active filters — for list header."""
        parts: list[str] = []
        if self.topic:
            parts.append(f'từ khoá "{self.topic}"')
        if self.attendee:
            parts.append(f"khách {self.attendee}")
        if self.date_from and self.date_to and self.date_from == self.date_to:
            parts.append(f"ngày {_fmt_iso_date_vi(self.date_from)}")
        elif self.date_from and self.date_to:
            parts.append(
                f"từ {_fmt_iso_date_vi(self.date_from)} đến {_fmt_iso_date_vi(self.date_to)}"
            )
        elif self.date_from:
            parts.append(f"từ {_fmt_iso_date_vi(self.date_from)}")
        elif self.date_to:
            parts.append(f"đến {_fmt_iso_date_vi(self.date_to)}")
        return " · ".join(parts)


def _fmt_iso_date_vi(iso: str) -> str:
    y, m, d = iso.split("-")
    return f"{int(d)}/{int(m)}/{y}"


# Explicit date range like "27/4-4/5" or "27/4/2026 - 4/5/2026"
_RE_DATE_RANGE = re.compile(
    r"(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?"
    r"\s*[-–—]\s*"
    r"(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?"
)
_RE_SINGLE_DATE = re.compile(r"^(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?$")
# "tháng 5" or "tháng 5/2026"
_RE_MONTH = re.compile(
    r"(?:tháng|thang)\s+(\d{1,2})(?:[/\-.](\d{2,4}))?",
    re.IGNORECASE,
)
_RE_PAGE = re.compile(r"^(?:p|trang)?\s*(\d{1,3})$", re.IGNORECASE)


def _week_range(base: date, offset_weeks: int = 0) -> tuple[str, str]:
    """Return (from_iso, to_iso) for the ISO week containing base+offset."""
    target = base + timedelta(weeks=offset_weeks)
    # Monday of that week
    monday = target - timedelta(days=target.weekday())
    sunday = monday + timedelta(days=6)
    return monday.isoformat(), sunday.isoformat()


def _month_range(year: int, month: int) -> tuple[str, str]:
    first = date(year, month, 1)
    # Last day = first of next month minus 1
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    last = nxt - timedelta(days=1)
    return first.isoformat(), last.isoformat()


# ── Clone parser ──────────────────────────────────────────────────────────────
# `tạo lịch giống #5 nhưng ngày 27/4 15h khách a@x.vn`
# Target forms: #id, "topic", ngày DD/MM, khách email, bare topic string.
# Override tokens (comma-separated OK): ngày / giờ / thời lượng / tên "..." / nội dung / khách / thêm khách.
_RE_CLONE_PREFIX = re.compile(
    r'^\s*(?:t[aạ]o\s+l[iị]ch|l[iị]ch)\s+(?:gi[ốôo]ng|nh[uư])\s+(.+)',
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class CloneOverrides:
    """Fields to override on the clone. Any None → keep source value."""
    topic: str | None = None
    start_date: date | None = None
    start_time: tuple[int, int] | None = None  # (hour, minute)
    duration_min: int | None = None
    agenda: str | None = None
    attendees_replace: list[str] | None = None
    attendees_add: list[str] | None = None

    @property
    def is_empty(self) -> bool:
        return not any([
            self.topic, self.start_date, self.start_time, self.duration_min,
            self.agenda, self.attendees_replace, self.attendees_add,
        ])


@dataclass
class CloneSpec:
    target: TargetSpec
    overrides: CloneOverrides


def parse_clone(text: str) -> CloneSpec | None:
    """Detect `tạo lịch giống <target> [nhưng <overrides>]`. None if not clone.

    Raises ParseError only if the prefix matches but target is missing —
    ambiguity with the main create parser is avoided by checking the
    `giống / như` keyword.
    """
    m = _RE_CLONE_PREFIX.match(text.strip())
    if not m:
        return None
    rest = m.group(1).strip()

    # Split on first "nhưng" / "nhung" keyword (case-insensitive, word boundary)
    split = re.split(r'\s+nh[uư]ng\s+', rest, maxsplit=1, flags=re.IGNORECASE)
    target_part = split[0].strip()
    override_text = split[1].strip() if len(split) > 1 else ""

    # 1) Target: #id first
    target = TargetSpec()
    m_id = _RE_TARGET_ID.search(target_part)
    if m_id:
        target.id = int(m_id.group(1))
        target_part = (target_part[: m_id.start()] + target_part[m_id.end():]).strip()

    # 2) Natural target specifiers (quoted topic / ngày / khách)
    leftover, natural = _extract_natural_target(target_part)
    target.topic = target.topic or natural.topic
    target.attendee = target.attendee or natural.attendee
    target.date = target.date or natural.date

    # 3) Any bare string leftover → treat as topic (chị gõ `tạo lịch giống Mentor MBOs nhưng ...`)
    if not target.topic and not target.id and leftover:
        bare = leftover.strip().strip('"\u201c\u201d\'').strip()
        if bare:
            target.topic = bare

    if target.is_empty:
        raise ParseError(
            "Em cần biết chị muốn clone lịch nào. "
            'VD: `tạo lịch giống #5 nhưng ngày 27/4 15h` hoặc '
            '`tạo lịch giống "Mentor MBOs" nhưng ngày mai`.'
        )

    overrides = _parse_clone_overrides(override_text)
    return CloneSpec(target=target, overrides=overrides)


def _parse_clone_overrides(text: str) -> CloneOverrides:
    """Parse the part after `nhưng`. Empty → no overrides (pure copy)."""
    out = CloneOverrides()
    s = text.strip()
    if not s:
        return out

    # Try multi-line labelled form first (same labels as create)
    labels = _extract_labels(s)
    if labels:
        if "time" in labels:
            start_dt, _rec = _parse_time_and_recurrence(labels["time"])
            out.start_date = start_dt.date()
            out.start_time = (start_dt.hour, start_dt.minute)
        if "duration" in labels:
            n = _parse_duration(labels["duration"])
            if n:
                out.duration_min = n
        if "agenda" in labels:
            out.agenda = labels["agenda"]
        if "attendees" in labels:
            emails = _parse_emails(labels["attendees"])
            if emails:
                out.attendees_replace = emails
        return out

    # Single-line form: iteratively strip known keyword tokens.

    # "thêm khách <emails>" — ADD (must run before "khách" to not be swallowed)
    m = re.search(
        r'(?:thêm|them)\s+(?:khách|khach)\s+'
        r'(?P<emails>(?:[\w.+\-]+@[\w\-]+\.[\w.\-]+[\s,]*)+)',
        s, re.IGNORECASE,
    )
    if m:
        out.attendees_add = _parse_emails(m.group("emails"))
        s = (s[: m.start()] + s[m.end():]).strip(" ,")

    # "khách <emails>" — REPLACE
    m = re.search(
        r'(?:khách|khach)\s+'
        r'(?P<emails>(?:[\w.+\-]+@[\w\-]+\.[\w.\-]+[\s,]*)+)',
        s, re.IGNORECASE,
    )
    if m and out.attendees_replace is None:
        emails = _parse_emails(m.group("emails"))
        if emails:
            out.attendees_replace = emails
            s = (s[: m.start()] + s[m.end():]).strip(" ,")

    # "tên \"...\"" or "tên <ABC>" — stops at comma or next keyword
    m = re.search(
        r'(?:tên|ten)\s+'
        r'(?:["\u201c\']([^"\u201d\']+)["\u201d\']'
        r'|([^,\n]+?))'
        r'(?=\s*(?:,|$|(?:thời|thoi|nội|noi|khách|khach|ngày|ngay|giờ|gio)\b))',
        s, re.IGNORECASE,
    )
    if m:
        out.topic = (m.group(1) or m.group(2) or "").strip()
        s = (s[: m.start()] + s[m.end():]).strip(" ,")

    # "thời lượng N phút|tiếng"
    m = re.search(
        r'(?:thời lượng|thoi luong)\s+(\d+\s*(?:phút|phut|p|giờ|gio|h|tiếng|tieng))',
        s, re.IGNORECASE,
    )
    if m:
        n = _parse_duration(m.group(1))
        if n:
            out.duration_min = n
        s = (s[: m.start()] + s[m.end():]).strip(" ,")

    # "nội dung <text>" — greedy until next known keyword or comma boundary
    m = re.search(
        r'(?:nội dung|noi dung)\s+'
        r'(?P<v>.+?)'
        r'(?=\s*(?:,|$|(?:tên|ten|khách|khach|thêm|them|ngày|ngay|giờ|gio|thời|thoi)\b))',
        s, re.IGNORECASE,
    )
    if m:
        out.agenda = m.group("v").strip()
        s = (s[: m.start()] + s[m.end():]).strip(" ,")

    # "ngày DD/MM[/YYYY]" labelled
    m = re.search(
        r"(?:ngày|ngay)\s+(\d{1,2})[/\-.](\d{1,2})(?:[/\-.](\d{2,4}))?",
        s, re.IGNORECASE,
    )
    if m:
        day = int(m.group(1)); month = int(m.group(2))
        year = _resolve_year(m.group(3), month, day)
        out.start_date = date(year, month, day)
        s = (s[: m.start()] + s[m.end():]).strip(" ,")
    else:
        # Relative: "ngày mai" / "hôm nay" / "hôm qua"
        low = s.lower()
        today = date.today()
        if re.search(r"\b(ngày mai|ngay mai|mai)\b", low):
            out.start_date = today + timedelta(days=1)
            s = re.sub(r"\b(ngày mai|ngay mai|mai)\b", "", s, flags=re.IGNORECASE).strip(" ,")
        elif re.search(r"\b(hôm nay|hom nay)\b", low):
            out.start_date = today
            s = re.sub(r"\b(hôm nay|hom nay)\b", "", s, flags=re.IGNORECASE).strip(" ,")

    # "giờ HHh[MM]" labelled
    m = re.search(
        r"(?:giờ|gio)\s+(\d{1,2})\s*(?:h|giờ|:)\s*(\d{0,2})\s*"
        r"(sáng|chiều|tối|trưa|đêm|sang|chieu|toi|trua|dem)?",
        s, re.IGNORECASE,
    )
    if m:
        hour = int(m.group(1)); minute = int(m.group(2) or "0")
        period = (m.group(3) or "").lower()
        if period in ("chiều", "chieu", "tối", "toi", "đêm", "dem") and hour < 12:
            hour += 12
        if period in ("sáng", "sang") and hour == 12:
            hour = 0
        out.start_time = (hour, minute)
        s = (s[: m.start()] + s[m.end():]).strip(" ,")

    # Bare `DD/MM[/YYYY]` if no ngày consumed
    if out.start_date is None:
        m = _RE_DATE.search(s)
        if m:
            day = int(m.group(1)); month = int(m.group(2))
            year = _resolve_year(m.group(3), month, day)
            out.start_date = date(year, month, day)
            s = (s[: m.start()] + s[m.end():]).strip(" ,")

    # Bare `HHh[MM]` if no giờ consumed
    if out.start_time is None:
        m = _RE_TIME.search(s.lower())
        if m:
            hour = int(m.group(1)); minute = int(m.group(2) or "0")
            period = (m.group(3) or "").lower()
            if period in ("chiều", "chieu", "tối", "toi", "đêm", "dem") and hour < 12:
                hour += 12
            if period in ("sáng", "sang") and hour == 12:
                hour = 0
            out.start_time = (hour, minute)

    return out


def parse_list_args(raw: str) -> ListQuery:
    """Parse the argument string passed to /list.

    Accepted forms (all optional, can combine — page number always last):
      /list                       → page 1, no filter
      /list 2                     → page 2
      /list OKRs                  → topic contains 'OKRs'
      /list khách lan@abc.com     → attendee filter
      /list tuần này              → current ISO week
      /list tuần sau / tuần trước → next/prev week
      /list hôm nay / mai / hôm qua
      /list tháng này             → current month
      /list tháng 5               → May (nearest future if past)
      /list tháng 5/2026
      /list 27/4                  → single day
      /list 27/4-4/5              → range
      /list 27/4/2026-4/5/2026
    """
    q = ListQuery()
    s = raw.strip()
    if not s:
        return q

    low = s.lower()

    # 1) Page number alone?
    m = _RE_PAGE.match(s)
    if m:
        q.page = max(1, int(m.group(1)))
        return q

    today = date.today()

    # 2) Keyword buckets first (consume if matched)
    replacements: list[tuple[re.Pattern, str]] = []

    if re.search(r"\b(tuần này|tuan nay)\b", low):
        q.date_from, q.date_to = _week_range(today, 0)
        s = re.sub(r"\b(tuần này|tuan nay)\b", "", s, flags=re.IGNORECASE).strip()
    elif re.search(r"\b(tuần sau|tuan sau|tuần tới|tuan toi)\b", low):
        q.date_from, q.date_to = _week_range(today, 1)
        s = re.sub(
            r"\b(tuần sau|tuan sau|tuần tới|tuan toi)\b", "", s, flags=re.IGNORECASE
        ).strip()
    elif re.search(r"\b(tuần trước|tuan truoc)\b", low):
        q.date_from, q.date_to = _week_range(today, -1)
        s = re.sub(r"\b(tuần trước|tuan truoc)\b", "", s, flags=re.IGNORECASE).strip()

    low = s.lower()
    if re.search(r"\b(hôm nay|hom nay|today)\b", low):
        q.date_from = q.date_to = today.isoformat()
        s = re.sub(
            r"\b(hôm nay|hom nay|today)\b", "", s, flags=re.IGNORECASE
        ).strip()
    elif re.search(r"\b(ngày mai|ngay mai|mai)\b", low):
        tomorrow = (today + timedelta(days=1)).isoformat()
        q.date_from = q.date_to = tomorrow
        s = re.sub(
            r"\b(ngày mai|ngay mai|mai)\b", "", s, flags=re.IGNORECASE
        ).strip()
    elif re.search(r"\b(hôm qua|hom qua|yesterday)\b", low):
        yesterday = (today - timedelta(days=1)).isoformat()
        q.date_from = q.date_to = yesterday
        s = re.sub(
            r"\b(hôm qua|hom qua|yesterday)\b", "", s, flags=re.IGNORECASE
        ).strip()

    low = s.lower()
    if re.search(r"\b(tháng này|thang nay)\b", low):
        q.date_from, q.date_to = _month_range(today.year, today.month)
        s = re.sub(r"\b(tháng này|thang nay)\b", "", s, flags=re.IGNORECASE).strip()
    else:
        m = _RE_MONTH.search(s)
        if m:
            month = int(m.group(1))
            year_raw = m.group(2)
            year = _resolve_year(year_raw, month, 1)
            q.date_from, q.date_to = _month_range(year, month)
            s = (s[: m.start()] + s[m.end():]).strip()

    # 3) Explicit date range / single date
    m = _RE_DATE_RANGE.search(s)
    if m and not q.date_from:
        d1 = int(m.group(1)); mo1 = int(m.group(2))
        y1 = _resolve_year(m.group(3), mo1, d1)
        d2 = int(m.group(4)); mo2 = int(m.group(5))
        y2_raw = m.group(6)
        # If end year not given, reuse start year
        y2 = (int(y2_raw) + 2000 if y2_raw and int(y2_raw) < 100
              else int(y2_raw) if y2_raw else y1)
        q.date_from = f"{y1:04d}-{mo1:02d}-{d1:02d}"
        q.date_to = f"{y2:04d}-{mo2:02d}-{d2:02d}"
        s = (s[: m.start()] + s[m.end():]).strip()
    else:
        ms = _RE_SINGLE_DATE.match(s)
        if ms and not q.date_from:
            d1 = int(ms.group(1)); mo1 = int(ms.group(2))
            y1 = _resolve_year(ms.group(3), mo1, d1)
            iso = f"{y1:04d}-{mo1:02d}-{d1:02d}"
            q.date_from = q.date_to = iso
            s = ""

    # 4) 'khách <email>' attendee filter
    m = _RE_TARGET_ATTENDEE.search(s)
    if m:
        q.attendee = m.group(1).lower()
        s = (s[: m.start()] + s[m.end():]).strip()

    # 5) trailing page number — "/list OKRs 2"
    tokens = s.split()
    if tokens:
        last = tokens[-1]
        if last.isdigit() and len(last) <= 3:
            q.page = max(1, int(last))
            tokens = tokens[:-1]
            s = " ".join(tokens).strip()

    # 6) whatever's left is the topic keyword
    leftover = s.strip().strip('"\u201c\u201d\'').strip()
    if leftover:
        q.topic = leftover

    return q
