# JA Scheduler Bot — Tài liệu Thiết kế Tính năng Chi tiết

**Phiên bản:** 1.0 (2026-04-26)
**Companion:** [01-system-design.md](01-system-design.md)

---

## Mục lục

1. [Tạo lịch — Work / Cá nhân HY / Clone](#1-tạo-lịch)
2. [Quản lý lịch — `/list`, detail, sửa, xoá](#2-quản-lý-lịch--list)
3. [Sửa nhanh từ chat (không qua /list)](#3-sửa-nhanh-từ-chat)
4. [Lịch lặp — sửa/xoá 1 buổi riêng](#4-lịch-lặp--sửaxoá-1-buổi-riêng)
5. [Notify-email toggle](#5-notify-email-toggle)
6. [Reminder 30 phút + Digest 7h sáng](#6-reminder--digest)
7. [Cảnh báo trùng lịch](#7-cảnh-báo-trùng-lịch)
8. [Sync drag-drop từ Calendar](#8-sync-drag-drop)
9. [External Calendar — đọc + sửa/xoá qua bot](#9-external-calendar)
10. [Backup pipeline (local + Drive)](#10-backup-pipeline)

---

## 1. Tạo lịch

### 1.1 Lịch công việc — One-time (Zoom + Calendar + invite)

**Trigger keyword:** `Tạo lịch`

**Input format:**
```
Tạo lịch "Tư vấn OKRs - Chị Lan":
- Thời gian: 22/4/2026 14:00
- Thời lượng: 30 phút
- Nội dung: Tư vấn gói Coaching OKRs
- Khách: lan@abc.com
```

**Parser (parser.parse_create):**
| Field | Regex / Logic |
|---|---|
| Topic | Trong cặp dấu `"..."` ngay sau `Tạo lịch` |
| Thời gian | `dd/mm/yyyy HH:MM` hoặc `HH:MM dd/mm/yyyy` |
| Thời lượng | `30 phút`, `1 tiếng`, `2h30` → minutes int |
| Nội dung | Phần text sau `Nội dung:` đến hết dòng |
| Khách | Email comma-separated, validated qua regex |

**Output:** `ParsedCommand(is_personal=False, recurring=None, ...)`.

**Flow:**
```
Parse → check trùng lịch (xem section 7) → format_confirm_preview
   ↓
Bot reply preview + 2 button [✅ Xác nhận tạo / ❌ Huỷ]
   ↓ (user confirm)
_do_create:
  1. zoom_client.create_meeting(topic, start, duration, attendees)
     → meeting_id, join_url, passcode (3 trường)
  2. format_calendar_description(cmd, zoom_*) → mô tả Calendar event chứa Zoom info
  3. calendar_client.create_event(
        summary="[John Academy] " + topic,
        description=above,
        attendees=cmd.attendees,
        send_updates="all",  # gửi mail invite
     ) → event_id, html_link
  4. db.insert_event(...) → row id (auto-increment)
  5. format_success_reply → Bot reply "✅ Đã tạo xong:..."
```

**Error handling:**
- Parse fail → `ParseError` với message tiếng Việt cụ thể (`"Em không hiểu thời gian, cho em xin định dạng dd/mm/yyyy HH:MM"`).
- Zoom fail → reply lỗi, KHÔNG tạo Calendar (atomic).
- Calendar fail (Zoom đã tạo) → log, reply warning, suggest manual delete Zoom.

### 1.2 Lịch công việc — Recurring (hàng tuần)

**Input format:**
```
Tạo lịch "Mentor MBOs 42":
- Thời gian: 8h30 sáng thứ 4 hàng tuần trong 12 tuần liên tiếp bắt đầu từ 20/5/2026
- Thời lượng: 120 phút
- Nội dung: Chương trình Mentor MBOs
- Mời khách: a@x.vn, b@y.vn
```

**Parser detect "thứ N hàng tuần trong M tuần liên tiếp bắt đầu từ DD/MM/YYYY":**
- "thứ 2/3/4/5/6/7" → BYDAY MO/TU/WE/TH/FR/SA
- "chủ nhật" → SU
- M = count weeks
- DD/MM/YYYY = first occurrence date

**Output:** `ParsedCommand(recurring={"byday": "WE", "count": 12})`.

**Flow giống one-time + thêm:**
- `zoom_client.create_meeting(type=8, recurrence={...})` — Zoom recurring meeting (tất cả buổi cùng meeting_id).
- `calendar_client.create_event(rrule="RRULE:FREQ=WEEKLY;BYDAY=WE;COUNT=12")`.
- `format_success_reply` list từng buổi (12 dòng).

### 1.3 Lịch HY cá nhân (Meet, private)

**Trigger keyword:** `HY` (thay vì `Tạo lịch`).

**Input format:** Y hệt work, chỉ đổi prefix:
```
HY "Mentor 1-1 Linh":
- Thời gian: 10h sáng thứ 6 hàng tuần trong 8 tuần liên tiếp bắt đầu từ 1/5/2026
- Thời lượng: 60 phút
- Nội dung: Coaching cá nhân
- Khách: linh@abc.com
```

**Khác biệt với work:**
| Aspect | Work | HY (cá nhân) |
|---|---|---|
| Provider | Zoom | Google Meet (auto-gen) |
| Calendar visibility | default | **private** |
| Description prefix | "Kính gửi anh/chị... John Academy..." | Plain "📅 Thời gian:..." |
| Calendar summary | `[John Academy] Topic` | `[HY] Topic` |
| DB row | provider='zoom', meet_join_url='' | provider='meet', zoom_*='' |
| Reminder format | 🔗 Zoom + ID + passcode | 🔗 Google Meet only |
| /list icon | 🎯 hoặc 🔁 | 🔒 (luôn) |

**Implementation đặc biệt — 2-step description:**
1. `calendar_client.create_event(with_meet=True, description="(Meet link sẽ xuất hiện sau)")` → Google trả `event` chứa `hangoutLink`.
2. `calendar_client.patch_description(event_id, description=full_with_meet_link)` — embed Meet link thật.

Nếu step 2 fail (Google API hiccup), event vẫn tạo OK với placeholder; non-fatal.

**Privacy guarantee:** sếp Đạt + đồng nghiệp share Calendar chỉ thấy busy-block (không xem được summary, description, attendees).

### 1.4 Clone lịch

**Trigger pattern:** `tạo lịch giống <target> [nhưng <overrides>]`

**Examples:**
```
tạo lịch giống #5
tạo lịch giống #5 nhưng ngày 27/4 15h
tạo lịch giống "Tư vấn OKRs" nhưng ngày mai, khách a@x.vn
tạo lịch giống #3 nhưng tên "OKRs v2", thêm khách b@y.vn
```

**Target syntax:**
- `#<id>` — lịch ID cụ thể.
- `"Tên lịch"` — tìm theo tên (gần đúng).

**Override fields:**
- `ngày DD/MM` hoặc `ngày mai/hôm nay/...`
- `giờ HH:MM`
- `thời lượng N phút`
- `tên "..."`
- `nội dung ...`
- `khách a@x.vn, b@y.vn` — REPLACE attendee list
- `thêm khách a@x.vn` — APPEND vào list

**Behavior:**
1. Lookup target row trong DB.
2. Nếu target có `recurring` → DROP recurring (clone thành one-time).
3. Apply overrides lên copy.
4. Run conflict check.
5. Standard preview → confirm → `_do_create` flow.

**Edge cases:**
- Target deleted (status=deleted) → vẫn cho clone (giúp re-tạo lịch đã huỷ).
- Multiple match cho `"Tên lịch"` → bot show numbered list để chị bấm chọn (disambiguation).

---

## 2. Quản lý lịch — `/list`

### 2.1 Default — `/list` (10 lịch gần nhất)

```
/list
```

→ Bot reply 10 lịch sort theo `start_local DESC`, mỗi dòng có:
- Số thứ tự (1-10) — bấm để xem detail.
- Icon: 🎯 (one-time bot tạo), 🔁 (recurring), 🔒 (HY cá nhân).
- Format: `🎯 27/4 14:00 · 🔒 Tư vấn OKRs - Chị Lan`.

### 2.2 Filter & Pagination

| Command | Mô tả |
|---|---|
| `/list 2` | Trang 2 (10 lịch/trang) |
| `/list OKRs` | Lọc keyword trong topic/agenda |
| `/list khách lan@abc.com` | Lọc theo email khách |
| `/list tuần này` | Date range: thứ 2-CN tuần hiện tại |
| `/list tuần sau / tuần trước` | Date range tương ứng |
| `/list hôm nay / mai / hôm qua` | 1 ngày |
| `/list tháng này / tháng 5 / tháng 5/2026` | Date range tháng |
| `/list 27/4` | 1 ngày cụ thể |
| `/list 27/4-4/5` | Khoảng ngày |
| `/list OKRs 2` | Keyword + trang |

**Implementation:**
- `parser.parse_list_args` → `ListQuery(filter, page, ...)`.
- `db.search_events(filter, limit=10, offset=(page-1)*10)`.
- `db.count_events(filter)` cho total page count.
- `format_list` render header + numbered buttons.

### 2.3 Detail view (sau khi bấm số)

```
🏷 *🔒 Tư vấn OKRs - Chị Lan* (id=5)
_🔒 Lịch HY cá nhân · private · Meet_
🔁 Thứ 4 hàng tuần, bắt đầu 22/4/2026 14:00 - 15:00 (12 buổi → 7/7/2026)
⏱ 60 phút
🎯 Tư vấn gói Coaching OKRs
👥 Khách:
  • lan@abc.com
🔗 [Meet](...)  |  🗓 [Calendar](...)
```

3 button:
- `✏️ Sửa` → menu 6 field.
- `🗑 Xoá` → confirm flow.
- `🔄 Sync` (chỉ hiện nếu có drift Calendar — xem section 8).

### 2.4 Edit menu (6 field)

| Field | Prompt | Implementation |
|---|---|---|
| Giờ/ngày | "Nhắn giờ/ngày mới (VD: `15h30 25/4/2026`)" | parse_time → update DB + Calendar + Zoom |
| Thời lượng | "Nhắn thời lượng mới (VD: `45 phút`)" | parse_duration → update |
| Thêm khách | "Nhắn email cần THÊM" | append to attendees, send_updates=all |
| Bỏ khách | "Nhắn email cần BỎ" | filter out, send_updates=all |
| Tên lịch | "Nhắn tên lịch mới" | update topic + Calendar summary |
| Nội dung | "Nhắn nội dung (agenda) mới" | update agenda + Calendar description |

**State machine** trong `ctx.chat_data`:
```python
{"edit_pending": {"row_id": 5, "field": "time"}}
```
Bot expect tin nhắn tiếp theo = giá trị mới.

**Confirm step:** sau khi parse giá trị mới, bot show preview với 2 button notify (xem section 5).

### 2.5 Delete flow

Bấm `🗑 Xoá` → bot show confirm với 2 button:
- `✅ Xoá + gửi mail` — Zoom delete + Calendar delete (sendUpdates=all) → khách nhận mail huỷ.
- `✅ Xoá (không mail)` — sendUpdates=none → âm thầm.

DB: `status='deleted'` (soft delete, không erase). `/list` mặc định filter `status='active'`.

---

## 3. Sửa nhanh từ chat

Thay vì `/list → bấm → ✏️ Sửa → bấm field → gõ giá trị`, chị có thể nhắn 1 dòng làm tất cả.

### 3.1 Sửa lịch mới nhất

```
sửa giờ 15h30
sửa giờ 15h30 25/4/2026
sửa thời lượng 45 phút
sửa tên Tư vấn OKRs v2
sửa nội dung Nội dung mới
thêm khách a@x.vn, b@y.vn
bỏ khách a@x.vn
xoá lịch
```

→ Bot tìm lịch mới nhất (status=active, sort by created_at DESC) → preview → confirm.

### 3.2 Bằng `#id`

```
sửa giờ 15h #5
thêm khách a@x.vn #5
xoá lịch #5
```

### 3.3 Bằng tên lịch

```
sửa giờ 15h30 "Tư vấn OKRs" ngày 25/4
xoá lịch "Tư vấn OKRs" ngày 25/4
xoá lịch khách lan@abc.com
sửa thời lượng 45 phút "Mentor MBOs"
```

**Disambiguation:** nếu nhiều lịch khớp → bot show numbered list:
```
🔎 Tìm thấy 3 lịch khớp "Tư vấn OKRs". Chị chọn lịch cần sửa:
1. 🎯 22/4 14:00 · Tư vấn OKRs - Chị Lan · id=5
2. 🎯 25/4 10:00 · Tư vấn OKRs - Chị Mai · id=8
3. 🎯 28/4 16:00 · Tư vấn OKRs - Chị Hồng · id=11
```
Chị bấm số → continue flow.

**Parser core: `parser.parse_edit_quick`** parse 3 phần:
- Verb: `sửa` / `xoá` / `thêm khách` / `bỏ khách`.
- Field + value: `giờ 15h30`, `tên "..."`, etc.
- TargetSpec: `#id` / `"name"` / `khách email` / `ngày DD/MM`.

---

## 4. Lịch lặp — sửa/xoá 1 buổi riêng

Recurring lịch có nhiều occurrences. Chị có thể action 1 buổi mà không ảnh hưởng các buổi khác.

### 4.1 Sửa 1 buổi riêng

Flow: `/list → bấm lịch lặp → ✏️ Sửa → "📝 Sửa 1 buổi riêng" → chọn buổi → Giờ / Thời lượng`.

**Implementation:**
- `_fetch_occurrences(row)` → expand từ `recurring` config + filter `cancelled_occurrences`.
- User chọn 1 occurrence → `_apply_occurrence_edit(row, occ_iso, field, value)`.
- Calendar API: dùng `events().instances(eventId, originalStart=occ_iso)` để get instance ID → patch chỉ instance đó (không touch parent recurring rule).

Limit: chỉ sửa được giờ/thời lượng cho 1 buổi (không sửa attendees riêng — Calendar limit).

### 4.2 Xoá 1 buổi riêng

Flow: `/list → bấm lịch lặp → 🗑 → "⦿ Chỉ 1 buổi" → chọn buổi`.

- Calendar: `events().delete(instanceId)` với `sendUpdates=all|none`.
- DB: thêm occurrence ISO vào `cancelled_occurrences` JSON array.
- Reminder + digest skip occurrence này tự động.

### 4.3 Xoá cả series

`/list → 🗑 → "🗑 Toàn bộ series"` → standard delete (status=deleted).

---

## 5. Notify-email toggle

Mỗi lần confirm sửa/xoá, bot show 2 button:
- `✅ … + gửi mail` → `sendUpdates="all"` → Google Calendar gửi mail update/huỷ cho khách.
- `✅ … (không mail)` → `sendUpdates="none"` → âm thầm, khách không biết.

Áp dụng cho:
- Sửa toàn bộ lịch (1.x.x edit menu).
- Xoá toàn bộ lịch.
- Sửa 1 buổi riêng (4.1).
- Xoá 1 buổi riêng (4.2).
- Xoá series.
- Sửa external Calendar (9.x).

Mặc định KHÔNG có default — user phải bấm 1 trong 2.

---

## 6. Reminder + Digest

### 6.1 30-min reminder

**Trigger:** `scheduler.reminder_loop` tick mỗi 60s.

**Logic:**
```python
now = _now_vn()  # naive Asia/Ho_Chi_Minh (UTC trên Render → convert)
window = [now + 28min, now + 32min]

# Lịch DB
for row, occ_iso in db.upcoming_unreminded(window):
    text = format_reminder(row, occ_iso)
    bot.send_message(chat_id, text)
    db.mark_reminded(row.id, occ_iso)  # add to reminders_sent JSON

# Lịch external Calendar
for occ in external_events.fetch_in_datetime_window(*window):
    if db.is_external_reminded(occ.calendar_event_id, occ.occurrence_iso):
        continue
    text = format_external_reminder(occ)
    bot.send_message(chat_id, text)
    db.mark_external_reminded(occ.calendar_event_id, occ.occurrence_iso)
```

**Format work:**
```
⏰ *Nhắc lịch ~30 phút nữa* — 14:00–14:30

🏷 *Tư vấn OKRs - Chị Lan* (id=5)
🎯 Tư vấn gói Coaching OKRs
⏱ 30 phút
👥 Khách:
  • lan@abc.com

🔗 [Zoom](https://us06web.zoom.us/j/...)
🆔 `87838324711` · 🔑 `300144`
```

**Format HY** (provider=meet):
```
⏰ *Nhắc lịch ~30 phút nữa* — 🔒 *Lịch HY cá nhân* · 09:00–10:00

🏷 *🔒 Check-in sức khoẻ* (id=11)
🎯 Tự review tuần
⏱ 60 phút
👥 Khách:
  (không)

🔗 [Google Meet](https://meet.google.com/daw-xohn-fmu)
```

**Format external**: ngắn gọn hơn (không có Zoom/Meet vì không phải bot tạo), chỉ link Calendar.

### 6.2 Daily digest 7h sáng

**Trigger:** `scheduler.daily_digest_loop` tick mỗi 60s.

**Window logic** (Phase 8 robustize):
```python
now = _now_vn()
if not (DIGEST_HOUR_START <= now.hour < DIGEST_HOUR_END_EXCL):  # 7 ≤ h < 19
    return
today = now.date().isoformat()
if db.get_meta("last_digest_date") == today:
    return  # đã fire hôm nay
```

Window 7-18h (thay vì đúng 7h) chống Render sleep qua 7h sáng — nếu service wake bất cứ lúc nào trong window vẫn fire 1 lần.

**Output:**
```
☀️ *Lịch hôm nay* — Thứ 2 27/4/2026

📋 *4 lịch* xếp theo giờ:

1. 🔁 *08:00–10:00* — Họp AI hàng tuần (id=10) · 👥 8
2. 🔒 *09:00–10:00* — 🔒 Check-in sức khoẻ (id=11)
3. 🎯 *14:00–14:30* — Tư vấn OKRs - Chị Lan (id=5) · 👥 1
4. 📅 *16:00–17:00* — All-hands (Calendar)

_🎯/🔁 lịch bot tạo · 🔒 lịch HY · 📅 lịch Calendar (không do bot tạo)_
_Gõ /list để xem chi tiết lịch bot tạo._
```

Sort theo `occurrence_iso` ASC. Merge lịch DB + external. Empty day → `📭 Hôm nay không có lịch nào...`.

### 6.3 `/today` — on-demand

`cmd_today` gọi cùng `_format_digest()` với `now=_now_vn()` → render ngay không cần đợi 7h sáng.

---

## 7. Cảnh báo trùng lịch

**Trigger:** create / clone / edit-time. Chạy trước preview.

**Logic** (`db.find_overlaps`):
1. Compute `[start, start+duration]` của lịch mới.
2. Cho mỗi lịch trong DB (status=active):
   a. Expand recurring → list of occurrence intervals.
   b. Filter occurrences trong khoảng ±7 ngày (giảm cost).
   c. Check overlap với new interval.
3. Return list `[(EventRow, occ_iso), ...]`.

**Render** (`format_conflict_warning`):
```
⚠️ *Cảnh báo trùng lịch* — phát hiện 2 lịch đã có overlap:
  · id=10 *Họp AI hàng tuần* lúc 27/4/2026 08:00–10:00
  · id=5 *Tư vấn OKRs* lúc 27/4/2026 14:00–14:30
_Chị vẫn có thể confirm nếu cố ý trùng._
```

User vẫn confirm được (chỉ cảnh báo, không block).

---

## 8. Sync drag-drop từ Calendar UI

Chị có thể kéo event trên Google Calendar UI (web/mobile) → Calendar UI update event đó. Bot không tự biết → cần lệnh sync.

### 8.1 `/sync` (latest)

Sync lịch mới nhất (status=active, by created_at DESC).

### 8.2 `/sync <id>`

Sync lịch cụ thể.

### 8.3 Auto-detect drift trong /list detail

Khi chị bấm vào lịch trong /list → bot fetch live Calendar → compare với DB:
- Nếu drift (start/end/attendees khác) → show banner ⚠️ + button `🔄 Sync (Calendar→Bot)`.
- Bấm → `_apply_drift`.

**Apply drift logic:**
```python
def _apply_drift(row, calendar_event):
    # Calendar = source of truth
    db.update_event(row.id, start=cal.start, end=cal.end, attendees=cal.attendees)
    if row.provider == "zoom":
        zoom_client.update_meeting(row.zoom_meeting_id, start=cal.start, ...)
    # provider=meet → skip Zoom (lịch HY không có Zoom)
```

**Limit:** /sync chỉ sync cấp **series** (parent recurring rule). Nếu chị kéo 1 instance riêng trên Calendar → dùng "Sửa 1 buổi riêng" qua /list.

---

## 9. External Calendar — đọc + sửa/xoá qua bot

Phase 5: bot đọc được lịch chị Yến tự tạo trên Google Calendar (không qua bot). Phase 6: sửa/xoá được luôn.

### 9.1 Đọc — `/list` date filter

Khi `/list` có lọc theo ngày (`tuần này`, `mai`, `27/4-4/5`, `tháng 5`...) → bot append section sau DB list:

```
📅 *3 lịch từ Calendar* _(không do bot tạo)_:
E1. 27/4 09:00 · Họp HR
E2. 27/4 16:00 · All-hands
E3. 28/4 11:00 · 1-1 với sếp Đạt
_Bấm nút `E1`/`E2`… để xem và sửa/xoá lịch Calendar._
```

`E#` button (callback `ext_sel:<idx>`) → detail view.

### 9.2 Detail external event

```
🏷 *Họp HR* _(từ Calendar — không do bot tạo)_
📅 Thứ 2, 27/4/2026, 09:00 - 10:00
⏱ 60 phút
🎯 (không)
👥 Khách:
  • hr@john.vn
🗓 [Mở Calendar](...)
```

3 button: `✏️ Sửa` / `🗑 Xoá` / `❌ Đóng`.

### 9.3 Edit external — same 6 field menu

Khác lịch bot tạo:
- KHÔNG có Zoom (event không phải bot tạo).
- DB không có row → state lưu trong `ctx.chat_data["list_externals"]` (resolve idx → occurrence khi callback).
- Calendar API: same `events().update/patch` flow với notify toggle.

### 9.4 Delete external

Same flow với `events().delete` + sendUpdates toggle.

**Edge case:** instance của recurring external → hiện tag `🔁 (1 buổi của lịch lặp — chỉnh chỉ ảnh hưởng buổi này)`. Calendar API tự xử lý qua `recurringEventId`.

---

## 10. Backup pipeline

### 10.1 Architecture

```
launchd daily 23:00 (~/Library/LaunchAgents/...backup.plist)
  └─ scripts/backup_all.sh (set -uo pipefail, không -e)
       ├─ scripts/backup_db.py
       │    └─ db._conn() → dump 3 bảng → SQL plain → gzip
       │       → data/backups/db_<TS>.sql.gz (~3KB)
       │       (cleanup file > 90 ngày)
       │
       ├─ scripts/backup_memory.sh
       │    └─ cp -p ~/.claude/projects/.../memory/*.md
       │       → data/memory_backups/<TS>/*.md (~40KB)
       │       (cleanup folder > 90 ngày)
       │
       └─ scripts/backup_to_drive.py
            └─ tar -czf /tmp/<TS>__data.tar.gz data/
               tar -czf /tmp/<TS>__bot.tar.gz bot/ (exclude __pycache__)
               DriveClient.upload → JA-Scheduler-Backups/ folder
               (cleanup file > 90 ngày trên Drive luôn)
```

### 10.2 backup_db.py

```python
def dump_database():
    parts = [header]
    with db._conn() as conn:
        for table in ["events", "bot_meta", "external_reminders_sent"]:
            parts.append(_dump_table(conn, table))  # CREATE + INSERT statements
    return "".join(parts) + "COMMIT;"

def save_dump(sql):
    out_path = BACKUP_DIR / f"db_{timestamp}.sql.gz"
    with gzip.open(out_path, "wt") as f:
        f.write(sql)
```

Restore: `gunzip -c file.sql.gz | sqlite3 restored.db` hoặc paste vào Turso shell.

### 10.3 backup_to_drive.py

Reuse `bot/drive_client.py`:
```python
client = DriveClient()  # OAuth refresh token + scope drive.file
folder_id = client.ensure_folder("JA-Scheduler-Backups")
for folder in ["data", "bot"]:
    archive = make_archive(folder, timestamp)
    client.upload_file(str(archive), drive_name=archive.name, parent_id=folder_id)
cleanup_old_drive_files(client, folder_id)  # delete > 90 ngày
```

Scope `drive.file` quan trọng:
- App chỉ thấy/sửa file mà chính nó tạo.
- KHÔNG đọc file khác trong Drive của chị.
- An toàn nhất.

**Trade-off đã biết:** nếu folder `JA-Scheduler-Backups` được tạo bởi MCP app khác → backup_to_drive.py không thấy → tạo folder mới cùng tên (chấp nhận duplicate, hoặc chị xoá folder cũ thủ công).

### 10.4 Retention

`BACKUP_RETENTION_DAYS = 90` ở 3 nơi:
- `backup_db.py` xoá file `data/backups/db_*.sql.gz` > 90 ngày.
- `backup_memory.sh` xoá folder `data/memory_backups/<TS>/` mtime > 90 ngày.
- `backup_to_drive.py` xoá file Drive `createdTime` > 90 ngày.

Disk footprint sau 90 ngày: < 11MB tổng (local + Drive).

### 10.5 Operate

| Action | Command |
|---|---|
| Status launchd | `launchctl list \| grep zoom-calendar-bot` |
| Trigger ngay | `launchctl start com.johnacademy.zoom-calendar-bot.backup` |
| Xem log | `tail -50 logs/backup.log` |
| Restart sau khi sửa plist | `launchctl unload ... && launchctl load ...` |
| Restore DB | `gunzip -c data/backups/db_*.sql.gz \| sqlite3 restored.db` |

Detail xem `scripts/BACKUP.md`.

---

## 11. Cross-cutting concerns

### 11.1 Validation
- Email regex: `^[\w.+-]+@[\w-]+\.[\w.-]+$`.
- Date: `dd/mm/yyyy` hoặc `dd/mm` (assume year hiện tại).
- Time: `HH:MM`, `Hh`, `HhMM`.
- Duration: regex `(\d+)\s*phút|tiếng|giờ|h`.

### 11.2 State management
- `ctx.chat_data` (per-chat dict do PTB cung cấp) lưu pending state:
  - `pending_create` — chờ confirm tạo.
  - `pending_edit` — chờ user gõ giá trị mới sau khi bấm field.
  - `pending_delete` — chờ confirm xoá.
  - `disambig_candidates` — list khi nhiều lịch khớp `"name"`.
  - `list_externals` — cache external occurrences cho callback E#.
  - `pending_drift` — drift detection cho /sync.
- `huỷ` keyword → clear toàn bộ `chat_data`.

### 11.3 Error UX
- Mọi lỗi user-facing → tiếng Việt thân mật, gợi ý hành động.
- Ví dụ: `"Em không hiểu định dạng giờ. Chị cho em ví dụ '14h30' hoặc '14:30' nhé."`.
- Errors logic (database, Zoom API): log full stack, reply ngắn gọn `"Có lỗi rồi chị, em ghi log để check."`.

### 11.4 Vietnamese-specific UX
- Days of week: thứ 2/3/4/5/6/7, chủ nhật.
- Number format: `60.000.000đ` (chấm phân cách).
- Date display: `27/4/2026` (dd/mm/yyyy).
- Title case không dùng — full sentence.
- Address user: chị (chị Yến). Self-address: em (bot).

---

## 12. Phase history

| Phase | Date | Commit | Mô tả |
|---|---|---|---|
| 0 | 2026-04-21 | (initial) | MVP one-time work meeting |
| 1 | 2026-04-22 | `61b1542` | Natural targeting + /list filter/paginate |
| 2 | 2026-04-22 | `466ea17` | Clone + conflict warning |
| 3 | 2026-04-22 | `657d007` | 30-min reminder + 7h digest |
| 4 | (deferred) | — | Bulk operations |
| 5 | 2026-04-23 | `5b9b934` | External Calendar read |
| 6 | 2026-04-23 | `3a9efda` | External Calendar edit qua /list |
| 7 | 2026-04-23 | `eaef174` | HY personal calendar (Meet, private) |
| 8a | 2026-04-24 | `9309391` | Hot-fix: /start /help split (4096 limit) |
| 8b | 2026-04-24 | `6f0f9c5` | Hot-fix: scheduler tz bug (UTC vs VN) |
| 8c | 2026-04-26 | `33e318f` | Robustize digest (window 7-18h) |
| 9 | 2026-04-26 | `697b651` | Local backup pipeline |
| 10 | 2026-04-26 | `4811674` | Google Drive backup (Phase 2 backup) |

---

## 13. Open questions / Decisions to make

1. **Bulk operations (Phase 4)** — chị Yến defer "B làm sau cùng". Chưa cần.
2. **Encryption at-rest cho backup** — DB chứa email khách + Zoom passcode. Hiện local + Drive private đủ. Cần thêm `age` nếu compliance yêu cầu.
3. **Multi-user expansion** — nếu sau này thêm team members, cần multi-chat_id whitelist + per-user OAuth. Hiện thiết kế single-user, không scale.
4. **Render Free vs Starter** — Free có cold start 30s. Nếu chị thấy delay khó chịu, upgrade $7/m.

---

*Cập nhật cuối: 2026-04-26 — sau Phase 10 (Drive backup pipeline complete).*
