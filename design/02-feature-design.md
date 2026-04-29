# JA Scheduler Bot — Tài liệu Thiết kế Tính năng Chi tiết

**Phiên bản:** 1.3 (2026-04-29)
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
11. [Cross-cutting concerns](#11-cross-cutting-concerns)
12. [Phase history](#12-phase-history)
13. [Open questions](#13-open-questions--decisions-to-make)
14. [Sổ thành viên công ty — `/members` + picker](#14-sổ-thành-viên-công-ty)
15. [Storage backend cho Members (Phase 12)](#15-storage-backend-cho-members-phase-12)
16. [Resolve tên thành email trong prompt (Phase 13)](#16-resolve-tên-thành-email-trong-prompt-phase-13)
17. [Phase 3.0 — `/whoami` public command](#17-phase-30--whoami-public-command)
18. [Phase 3 — Multi-user architecture](#18-phase-3--multi-user-architecture)
19. [Phase 3 — DB schema + migration](#19-phase-3--db-schema--migration)
20. [Phase 3 — Permission matrix + chat_mode flow](#20-phase-3--permission-matrix--chat_mode-flow)
21. [Phase 3 — Reply confirm gộp creator info](#21-phase-3--reply-confirm-gộp-creator-info)
22. [Phase 3 — `/mylist` + `/list_users` + `/audit`](#22-phase-3--mylist--list_users--audit)
23. [Phase 3 — Setup checklist (Render + GH + Drive)](#23-phase-3--setup-checklist-render--gh--drive)
24. [Phase 3 — Bug fixes hậu deploy](#24-phase-3--bug-fixes-hậu-deploy)

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

## 14. Sổ thành viên công ty

**Mục tiêu:** mỗi lần tạo lịch, chị Yến không phải gõ tay email khách (dễ sai chính tả, mất thời gian). Bot có sẵn sổ danh sách thành viên/đối tác → chị bấm chọn nhanh, hoặc vẫn gõ email mới như cũ.

**Nguyên tắc:** picker là **bổ sung**, không thay thế. Chị có thể (a) gõ Khách như cũ, (b) bấm sổ chọn nhanh, hoặc (c) kết hợp cả hai.

### 14.1 Storage

**File:** `data/members.json` (JSON, edit tay được, đi kèm backup pipeline ở Section 10).

```json
{
  "version": 1,
  "members": [
    {"name": "Chị Hải Yến", "email": "nguyenthihaiyen@john.vn", "title": "PM dự án"},
    {"name": "Sếp Đạt",     "email": "dat@john.vn",            "title": "CEO"},
    {"name": "Linh",        "email": "linh@john.vn",           "title": "PM"}
  ]
}
```

| Field | Bắt buộc | Ghi chú |
|---|---|---|
| `name` | ✓ | Tên hiển thị trong picker (VD "Chị Lan", "Đạt (CEO)") |
| `email` | ✓ | Khoá unique. Validate qua regex giống Khách trong tạo lịch |
| `title` |  | Chức danh / phòng ban — render mờ sau tên |

**Module:** `bot/directory.py` expose:
- `list_members() -> list[Member]` — đọc + cache theo mtime, hot-reload khi file đổi.
- `add_member(name, email, title='') -> Member` — append, atomic write tmp+rename.
- `remove_member(email) -> bool` — xoá theo email (case-insensitive), trả True nếu có.
- `find_by_email(email) -> Member | None` — tra ngược (dùng cho future: hiển thị tên kèm email trong reminder/preview).

**Edge cases:**
- File chưa tồn tại → trả list rỗng + bot gợi ý `/members add` lần đầu.
- File JSON hỏng → log error, trả list rỗng (không crash bot).
- Duplicate email khi add → reject, gợi ý `/members rm` trước.

### 14.2 `/members` command

| Dạng | Hành động |
|---|---|
| `/members` | Liệt kê toàn bộ sổ (1 dòng / người, format `name · title · email`) |
| `/members add <email> <name>` hoặc `/members add <email> <name> · <title>` | Thêm thành viên |
| `/members rm <email>` | Xoá thành viên |

Validate email regex giống parser tạo lịch. Reply tiếng Việt thân mật.

### 14.3 Picker UI — flow chuẩn

**Trigger:** trong preview tạo lịch (work / HY / clone), bot kèm thêm nút `📇 Sổ thành viên` ở keyboard:

```
[✅ Xác nhận tạo] [❌ Huỷ]
[📇 Sổ thành viên]
```

Bấm → bot edit message hiện tại sang panel directory:

```
📇 *Sổ thành viên công ty* — bấm để THÊM khách vào lịch
✓ = đã có trong khách mời

1. ✓ Chị Lan · Đối tác Coaching · lan@abc.com
2.   Đạt · CEO · dat@john.vn
3.   Linh · PM · linh@john.vn
4.   Trang · Sales · trang@john.vn
…
```

Inline keyboard:
```
[1] [2] [3] [4]               ← toggle 4 người/hàng
[5] [6] [7] [8]
[📋 Chọn tất cả] [🔄 Bỏ chọn] ← bulk actions (chỉ hiện khi áp dụng được)
[◀]  [1/2]  [▶]               ← page nav (8 người/trang)
[✅ Xong]  [❌ Huỷ]
```

- Bấm số → toggle email vào / ra `selected_emails`. Bot edit lại panel với `✓`/`·` cập nhật.
- Bấm **📋 Chọn tất cả** → tick toàn bộ thành viên trong sổ (kể cả những trang khác). Hữu ích khi mời cả team rồi untick 1-2 người vắng. Nút chỉ hiện khi còn người chưa tick.
- Bấm **🔄 Bỏ chọn** → reset về base (xoá hết tick mới, giữ nguyên ✓ của khách đã có sẵn). Nút chỉ hiện khi đã có ai đó được tick.
- Bấm `✅ Xong` → bot quay lại preview tạo lịch với `attendees` = base attendees ∪ selected_emails (dedupe, preserve order).
- Bấm `❌ Huỷ` → khôi phục preview, không thay đổi attendees.

### 14.4 Picker tích hợp vào edit menu

Khi chị bấm `➕ Thêm khách` trong edit menu (Section 2.4) hoặc external edit (Section 9.3), prompt vẫn cho phép gõ email tay, **đồng thời** kèm nút `📇 Sổ thành viên`:

```
✏️ Nhắn email cần THÊM (VD: a@x.vn, b@y.vn) — hoặc:
[📇 Sổ thành viên]
```

Bấm sổ → mở panel cùng cơ chế. Bấm `✅ Xong` → emails được chọn coi như giá trị "Thêm khách" → đi vào `_parse_edit(field='att_add', text=','.join(emails))` → preview confirm bình thường (có notify toggle).

### 14.5 State machine

`ctx.chat_data["dir_mode"]`:
```python
{
    "kind": "create" | "edit_add" | "ext_add",
    "page": int,                     # 1-based
    "selected_emails": list[str],    # đang trong giỏ
    "event_id": int | None,          # khi kind=edit_add
}
```

**Lifecycle:**
- Set khi user bấm `dir_open:<kind>`. `selected_emails` khởi tạo từ attendees hiện có (cho hiển thị `✓`) — KHÔNG dùng để dedupe khi merge (xem dưới).
- Update mỗi khi toggle / page nav.
- Pop khi `✅ Xong` hoặc `❌ Huỷ` hoặc gõ `huỷ` (Section 11.2).

**Merge logic khi `✅ Xong`:**
- `kind="create"`: `cmd.attendees = list(dict.fromkeys([*cmd.attendees, *picked_new]))` với `picked_new = selected − base_attendees`. Đảm bảo: nếu chị bỏ tick 1 email vốn đã có trong lịch, KHÔNG remove email đó (picker chỉ ADD, không REMOVE — tránh nhầm lẫn).
- `kind="edit_add"`: tương tự, nhưng đi qua `_parse_edit(row, "att_add", ", ".join(picked_new))`. Nếu `picked_new` rỗng → reply "Chị chưa chọn ai mới" và không vào confirm.
- `kind="ext_add"`: tương tự `edit_add` cho external occ.

### 14.6 Callback scheme

| Callback data | Ý nghĩa |
|---|---|
| `dir_open:create` | Mở picker từ create preview |
| `dir_open:edit:<event_id>` | Mở picker từ ed_f:att_add prompt |
| `dir_open:ext` | Mở picker từ ext_ed_f:att_add prompt |
| `dir_p:<page>` | Page nav |
| `dir_t:<idx>` | Toggle thành viên ở index toàn cục `idx` |
| `dir_done` | Apply selection, route lại flow gốc theo `kind` |
| `dir_cancel` | Huỷ, route lại preview/prompt cũ |

`idx` là **index trong list_members() đầy đủ** (không phụ thuộc page) → ổn định khi page nav.

### 14.7 Edge cases

- **Sổ rỗng** → khi mở picker, panel hiển thị `📭 Sổ thành viên trống. Gõ /members add <email> <name> để bắt đầu.` + nút `❌ Đóng`.
- **Sổ thay đổi giữa session** (chị mở picker rồi `/members add` ở tab khác) → mtime cache invalidate; lần next render panel sẽ reload, idx có thể shift. Chấp nhận trade-off; selection trong giỏ vẫn giữ vì lưu theo email chứ không idx.
- **Email trong sổ trùng email user gõ tay** trong câu lệnh tạo lịch → dedupe khi merge, không tạo trùng.
- **Page out-of-range** (sau khi xoá member) → clamp về `total_pages`.

### 14.8 Help text update

Trong `/help` (Part 1), section "2. TẠO LỊCH" sau ví dụ Khách thêm 1 dòng:
> _💡 Trong preview, bấm 📇 Sổ thành viên để chọn email từ danh sách công ty thay vì gõ tay._

Trong section riêng "📇 SỔ THÀNH VIÊN":
```
/members                      ← liệt kê
/members add a@x.vn Chị Lan   ← thêm
/members rm a@x.vn            ← xoá
```

---

## 15. Storage backend cho Members (Phase 12)

**Vấn đề:** Phase 11 dùng `data/members.json`. Render Free filesystem ephemeral → mọi thay đổi qua `/members add` từ Telegram sẽ bị **mất khi service restart** (cold-start sau 15 phút idle, hoặc redeploy). Chị Yến không edit được sổ qua bot từ điện thoại.

**Giải pháp:** dual backend giống `bot/db.py`:

| Backend | Khi nào dùng | Persistence |
|---|---|---|
| **Turso libSQL** (production) | `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` có trong env | ✅ Persistent qua restart |
| **JSON file** (local dev) | Không có Turso credentials | File `data/members.json` |

### 15.1 Schema

Thêm vào `_SCHEMA_STATEMENTS` của `bot/db.py`:

```sql
CREATE TABLE IF NOT EXISTS members (
    email TEXT PRIMARY KEY,           -- normalized lowercase
    name TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_members_sort
    ON members (sort_order, email);
```

`sort_order`: stable position cho picker — newly added member nhận `MAX(sort_order) + 1`. Picker UI render theo thứ tự này.

### 15.2 DB API

`bot/db.py` thêm 4 hàm:

| Hàm | Use |
|---|---|
| `list_members_db()` | Fetch toàn bộ rows, sort by `sort_order, email` |
| `insert_member(email, name, title, sort_order=None)` | UPSERT — `ON CONFLICT(email) DO UPDATE` |
| `delete_member(email)` | DELETE — trả `True` nếu có dòng bị xoá |
| `count_members()` | Cho seed-on-empty check (xem 15.3) |

### 15.3 One-time seed từ JSON

Lần đầu deploy với Turso, bảng rỗng. Để chị Yến không phải `/members add` lại từ đầu:

```python
def _maybe_seed_from_json():
    if _turso_seeded: return
    if db.count_members() > 0:
        _turso_seeded = True
        return
    json_members = _list_members_json()
    for i, m in enumerate(json_members):
        db.insert_member(email=m.email, name=m.name, title=m.title, sort_order=i)
    _turso_seeded = True
```

Idempotent (singleton flag + count check). Sau khi seed, JSON file giữ nguyên làm artifact lịch sử nhưng không còn được đọc khi Turso configured.

### 15.4 Backup pipeline cập nhật

`scripts/backup_db.py` `TABLES = ["events", "bot_meta", "external_reminders_sent", "members"]` — Phase 10 launchd daily 23:00 sẽ dump cả members table. Restore via `gunzip -c db_*.sql.gz | sqlite3 ...` hoặc paste vào Turso shell.

### 15.5 Migration/operate

| Action | Cách làm |
|---|---|
| Lần đầu deploy với Turso | Push code → bot tự seed từ `data/members.json` đang có |
| Bulk import danh sách lớn | Edit `data/members.json` local + commit + push (chạy seed lần đầu) HOẶC viết SQL `INSERT` paste vào Turso shell |
| Sau seed, edit qua bot | `/members add <email> <name>` từ Telegram → ghi Turso, persistent |
| Local dev không cần Turso | Bot tự fallback JSON, dev workflow không đổi |

---

## 16. Resolve tên thành email trong prompt (Phase 13)

**Mục tiêu:** Telegram bot không hỗ trợ inline autocomplete khi user đang gõ tin nhắn (chỉ thấy text khi user bấm Send). Để mô phỏng workflow "gõ tên → bot tự thay thành email", em parse dòng `Khách:` thông minh hơn — chấp nhận **cả tên member trong sổ lẫn email**, mix tự do với dấu phẩy.

### 16.1 UX

Chị Yến gõ:
```
Tạo lịch "Tư vấn OKRs - Chị Lan":
- Thời gian: 22/4/2026 14:00
- Thời lượng: 30 phút
- Nội dung: Tư vấn gói Coaching OKRs
- Khách: Lan, Đạt, abc@external.com
```

Bot resolve:
- `Lan` → tra sổ → match `Member(name="Chị Lan", email="lan@abc.com")` → email `lan@abc.com`
- `Đạt` → match `Member(name="Sếp Đạt", email="dat@john.vn")` → email `dat@john.vn`
- `abc@external.com` → đã là email hợp lệ → giữ nguyên

Preview hiện 3 khách. Nếu một token không match → bot reply preview kèm warning, chị có thể (a) sửa text rồi gửi lại, (b) `/members add` rồi gửi lại, hoặc (c) bấm 📇 picker để chọn từ sổ.

### 16.2 Match policy

`directory.find_by_name(query)` trả members theo thứ tự ưu tiên:

1. **Exact name** (case-insensitive) → ưu tiên cao nhất.
2. **Prefix** — tên bắt đầu bằng query.
3. **Substring** — query xuất hiện ở giữa tên.

| Token | Sổ có | Kết quả |
|---|---|---|
| `Lan` | "Chị Lan" | exact-prefix match ⇒ ✓ |
| `lan` | "Chị Lan" | substring match ⇒ ✓ |
| `Đạt` | "Sếp Đạt" | substring match ⇒ ✓ |
| `Linh` | "Linh", "Linh Trang" | 2 match → ambiguous, error |
| `@lan` | "Chị Lan" | prefix `@` strip rồi match ⇒ ✓ |
| `xyz` | (không có) | error: "không tìm thấy 'xyz' trong sổ" |
| `lan@abc.com` | (bất kỳ) | email hợp lệ → giữ nguyên |

### 16.3 API

`bot/directory.py`:

```python
@dataclass
class ResolutionResult:
    raw: str                     # token gốc
    email: str | None            # email cuối (None = fail)
    member: Member | None        # member matched (None nếu là email gõ tay)
    ambiguous: list[Member]      # nếu nhiều match
    error: str | None            # message tiếng Việt nếu fail

def resolve_token(token: str) -> ResolutionResult: ...

def resolve_attendees_line(line: str) -> tuple[list[str], list[ResolutionResult]]:
    """Phân tích cả dòng "Khách: a, b, c" → (resolved_emails, problems)."""
```

`resolve_attendees_line` split theo `[,;\n]+`, dedupe email, accumulate problems.

### 16.4 Tích hợp vào flow

| Flow | Tích hợp |
|---|---|
| Tạo lịch (work + HY) | `_resolve_attendees_into_cmd(cmd)` chạy ngay sau `parse_command(text)`. Ghi đè `cmd.attendees` + lưu warnings vào `cmd.attendees_problems` |
| Edit "➕ Thêm khách" / "➖ Bỏ khách" | `_parse_edit(row, "att_add", text)` gọi `_resolve_attendees_for_edit(text)` thay cho `parse_edit_emails`. Nếu có problem → raise ParseError với message rõ ràng |
| External edit | _Phase 13.1_ — chưa làm, fallback regex-only. (External edit khách hiếm, chị có 📇 picker rồi.) |
| Clone overrides (`khách x, y`) | _Phase 13.1_ — giữ regex-only ban đầu. Chị có thể `/members add` trước khi clone nếu cần dùng tên |

### 16.5 Warning trong preview

`format_confirm_preview` thêm block khi `cmd.attendees_problems` non-empty:

```
👥 *Khách mời* (2 người):
  • lan@abc.com
  • dat@john.vn

⚠️ *Em không hiểu vài người trong dòng Khách:*
  • Tên "Linh" khớp 2 người trong sổ — em không biết chọn ai.
  • Em không tìm thấy "ABC XYZ" trong sổ và đây không phải email.
_Chị sửa lại bằng email đầy đủ, hoặc gõ `/members add <email> <tên>` rồi gửi lại lệnh._
```

User vẫn confirm được — bot tạo lịch với danh sách đã resolve được. Token không hiểu thì không invite ai, chị phải edit sau hoặc gửi lại.

### 16.6 Trade-off

- **Vẫn giữ option gõ email tay 100%** — nếu chị không muốn rely sổ, gõ `lan@abc.com, dat@john.vn` vẫn work hoàn hảo.
- **Telegram không thể inline-autocomplete** → workflow tốt nhất em làm được là "gõ rồi resolve", chấp nhận đôi lúc cần edit lại nếu typo. Picker 📇 trong preview (Phase 11) vẫn là fallback nhanh.
- **Ambiguity** chưa interactive (không cho user bấm chọn 1 trong N member match) — Phase 13.2 nếu cần. Hiện chấp nhận chị tự gõ tên đầy đủ hơn (VD "Chị Linh" thay vì "Linh") để không ambiguous.

### 16.7 Shortcut `/all` (Phase 13.1)

Mời cả team trong 1 token. Bot expand thành toàn bộ members trong sổ.

**Token nhận:** `/all`, `all`, `@all`, `/all member`, `/all members`, `tất cả`, `tat ca`, `tất cả thành viên`, `toàn bộ`, `toan bo` (case-insensitive).

**Mix tự do:**
```
- Khách: /all, abc@external.com
```
→ 10 members trong sổ + abc@external.com = 11 khách.

```
- Khách: Toàn, /all, abc@x.vn
```
→ /all expand cả 10 (Toàn đã có trong sổ, dedupe) + abc@x.vn = 11 khách.

**Implementation:** trong `directory.resolve_attendees_line`, detect token là `_is_all_shortcut(tok)` thì expand bằng `list_members()`, dedupe theo `seen` set.

---

## 17. Review picker — sửa danh sách khách trong preview (Phase 14)

**Vấn đề:** sau khi resolve `/all` → 10-11 khách. Chị muốn untick 1-2 người vắng mà không mở picker thêm. UX hiện tại bắt chị phải bấm 📇 (add picker), chỉ cho ADD chứ không REMOVE.

**Giải pháp:** thêm 1 picker thứ 2 — **review picker** — chỉ làm REMOVE.

### 17.1 Trigger

Preview tạo lịch giờ có 4 nút (khi attendees non-empty):
```
[✅ Xác nhận tạo] [❌ Huỷ]
[📇 Thêm từ sổ]   [📋 Sửa danh sách]
```

`📋 Sửa danh sách` chỉ hiện khi `cmd.attendees` có ít nhất 1 người (không có khách thì không có gì để sửa).

### 17.2 Panel UI

```
📋 *Danh sách khách hiện tại* (11 người) — bấm số để BỎ:

1. *MXD* · maixuandat@okrs.vn
2. *Thuỳ* · vukimthuy@john.vn
…
10. *Yến* · nguyenthihaiyen@john.vn
11. _ngoài sổ_ · abc@external.com

[1] [2] [3] [4]
[5] [6] [7] [8]
[9] [10] [11]
[🗑 Bỏ tất cả]  [↩️ Quay lại preview]
```

- Mỗi khách hiển thị tên (nếu trong sổ) hoặc tag `_ngoài sổ_` (nếu là email tay không thuộc sổ).
- Bấm số → bỏ người đó, panel tự re-render với list rút gọn.
- `🗑 Bỏ tất cả` → clear sạch, panel hiện empty state với hint.
- `↩️ Quay lại preview` → confirm danh sách hiện tại, quay về preview.

### 17.3 State

Không cần state riêng. Picker này thao tác trực tiếp trên `ctx.chat_data["pending"].attendees` (cmd object). Mỗi click = mutation + re-render.

Khác với add picker (Phase 11) cần lưu `dir_mode["selected_emails"]` riêng, review picker đơn giản hơn vì:
- Không có "preview state" — mọi thay đổi apply ngay.
- Không cần "Cancel" semantic — `↩️ Quay lại` chỉ là navigation, không revert.

### 17.4 Callbacks

| Callback | Tác dụng |
|---|---|
| `rev_open:create` | Mở review panel từ create preview |
| `rev_rm:<idx>` | Xoá khách ở index `idx` (theo thứ tự `cmd.attendees`) |
| `rev_clear` | Xoá tất cả khách |
| `rev_back` | Re-render preview với attendees hiện tại |

### 17.5 Tích hợp cùng các flow khác

Phase 14 hiện chỉ hỗ trợ create flow. Edit "Bỏ khách" (`ed_f:att_rm`) đã có flow riêng — gõ email cần bỏ. External edit cũng tương tự.

Có thể mở rộng review picker cho edit flow (Phase 14.1) nếu chị thấy hữu ích, nhưng MVP này focus create vì đó là chỗ chị dùng `/all` nhiều nhất.

### 17.6 Combined workflow ví dụ

```
1. Chị gõ:
   Tạo lịch "Họp full team":
   - Thời gian: 30/4/2026 14:00
   - Thời lượng: 60 phút
   - Nội dung: Sync hàng tuần
   - Khách: /all, abc@external.com

2. Bot resolve → preview hiện 11 khách. Keyboard (sổ đã full
   → nút 📇 Thêm từ sổ tự ẩn — xem 17.7):
   [✅ Xác nhận tạo] [❌ Huỷ]
   [📋 Sửa danh sách]

3. Chị bấm 📋 Sửa danh sách → review panel 11 khách.

4. MXD và Hà vắng → chị bấm số 1 (MXD) → còn 10. Bấm số 6 (Hà mới — index đã shift sau khi xoá MXD!) → còn 9.
   ⚠️ Lưu ý: index re-number sau mỗi lần xoá. Chị nên xoá từ DƯỚI lên trên để index ổn định, hoặc nhìn tên kỹ trước khi bấm.

5. Chị bấm ↩️ Quay lại preview → preview hiện 9 khách. Nút 📇 Thêm từ sổ
   xuất hiện trở lại (sau khi xoá đã có member chưa thuộc danh sách).

6. Bấm ✅ Xác nhận tạo → bot tạo Zoom + Calendar + invite 9 người.
```

### 17.7 Logic ẩn nút thông minh (Phase 14.1)

Sau khi có cả 2 picker, keyboard preview có thể trở nên thừa thãi nếu nút không có tác dụng. Em ẩn nút theo trạng thái thực tế của `cmd.attendees` ↔ sổ:

| Tình huống | 📇 Thêm từ sổ | 📋 Sửa danh sách |
|---|:---:|:---:|
| 0 khách | ✅ hiện | ❌ ẩn (chưa có ai để sửa) |
| 1 khách trở lên + còn member sổ chưa tick | ✅ hiện | ✅ hiện |
| Tất cả members sổ đều thuộc danh sách (vd sau `/all`) | ❌ ẩn (không thêm được) | ✅ hiện |
| Sổ rỗng (chưa có member nào) | ❌ ẩn | ✅ hiện nếu có khách |

**Implementation** — helper `_preview_keyboard_for(cmd)` tự compute:

```python
def _preview_keyboard_for(cmd) -> InlineKeyboardMarkup:
    attendees_lower = {e.lower() for e in (cmd.attendees or [])}
    has_unpicked = any(
        m.email not in attendees_lower for m in directory.list_members()
    )
    return _create_preview_keyboard(
        has_attendees=bool(cmd.attendees),
        has_unpicked_in_directory=has_unpicked,
    )
```

Áp dụng tại 4 call sites: `handle_text` (parse mới), `_preview_clone` (clone), `_exit_dir_mode_back_to_create` (đóng add picker), `rev_back` (đóng review picker). Mỗi lần preview re-render → keyboard tự cập nhật theo trạng thái mới.

---

## 18. Phase history (cập nhật)

(Bảng ở Section 12 — lịch sử Phase 0-10. Bổ sung:)

| Phase | Date | Mô tả |
|---|---|---|
| 11 | 2026-04-27 | Sổ thành viên công ty (`data/members.json`, `/members`, picker integrate vào create + edit flows) |
| 12 | 2026-04-27 | Turso backend cho members (persistent qua Render restart, JSON fallback local dev) + auto-seed từ JSON lần đầu |
| 13 | 2026-04-27 | Resolve tên member trong dòng `Khách:` của prompt — gõ "Lan, Đạt, abc@external.com" thay vì email đầy đủ. Áp dụng cho create + edit "Thêm khách" / "Bỏ khách" |
| 13.1 | 2026-04-27 | Shortcut `/all` (và alias `tất cả`, `toàn bộ`...) trong dòng Khách → expand thành toàn bộ sổ thành viên. Mix tự do với email khách ngoài. |
| 14 | 2026-04-27 | Review picker — nút `📋 Sửa danh sách` trong preview, mở panel list toàn bộ khách hiện tại (in/out sổ), bấm số untick từng người. Combo với /all = "tick all rồi xoá bớt". |
| 14.1 | 2026-04-27 | UX cleanup — ẩn `📇 Thêm từ sổ` khi tất cả members sổ đã thuộc danh sách (vd sau /all), ẩn `📋 Sửa danh sách` khi chưa có khách. Helper `_preview_keyboard_for(cmd)` đảm bảo nhất quán ở 4 call sites. |
| 15 | 2026-04-28 | Reliable reminder + digest qua GitHub Actions trigger độc lập (không phụ thuộc Render uptime). |

---

## 19. Reliable reminder + digest (Phase 15)

**Vấn đề phát hiện 2026-04-28:**

- Buổi sáng nay 09:00 (Mentor MBO 39) → reminder ~8:30 **không gửi**. `external_reminders_sent` rỗng cho ngày đó → bot chưa từng tick reminder loop trong window 28-32 phút.
- Digest 7h sáng cũng fire muộn (ghi nhận `last_digest_date = 2026-04-28` nhưng có lẽ sau 7h vài giờ).

**Root cause:** Render Free sleep sau 15 phút không activity. GitHub Actions cron `*/10` (Phase 9) ping mỗi 10 phút **theo lý thuyết** đủ keep awake, nhưng **thực tế cron có thể trễ 5-15 phút** trong giờ cao điểm. Một ping bị trễ → gap > 15p → bot ngủ → bỏ lỡ reminder/digest window.

### 19.1 Fix 2 lớp

**Lớp 1 — tighten keep-alive** (`.github/workflows/keep-alive.yml`):
```yaml
- cron: "*/5 * * * *"   # was */10
```
Giảm xác suất gap > 15p. Nhưng vẫn không guarantee 100% — cron jitter có thể tạo gap 20p+ trong worst case.

**Lớp 2 — trigger độc lập từ GitHub Actions** (chính thức bền vững):

| Workflow | Schedule | Tác dụng |
|---|---|---|
| `daily-digest.yml` | `0 0 * * *` (00:00 UTC = 07:00 VN) | Chạy `scripts/trigger_digest.py` — đọc Turso + Calendar, gửi digest |
| `reminders.yml` | `*/5 * * * *` | Chạy `scripts/trigger_reminders.py` — check window 25-35p trước event, gửi reminder cho lịch chưa được mark sent |

GitHub Actions runner tự nó là Linux VM riêng, **không phụ thuộc Render uptime**. Workflow chạy trên môi trường sạch:
- Checkout repo
- `pip install -r requirements.txt`
- Set env từ GitHub Secrets (TELEGRAM_*, TURSO_*, GOOGLE_*, ZOOM_*, CONTACT_*)
- Chạy script

### 19.2 Idempotency

Cả 2 trigger đều idempotent với internal scheduler trong Render bot:

- **Digest** dedupe qua `bot_meta.last_digest_date` — workflow check `if last_digest_date == today: skip`. Internal scheduler cũng check cùng meta. Bên nào fire trước thì bên kia skip.
- **Reminder** dedupe qua `events.reminders_sent` (DB lịch bot) và `external_reminders_sent` (DB lịch external). `mark_reminded()` / `mark_external_reminded()` check `INSERT OR IGNORE`. Cả 2 nguồn (Render bot + GitHub Actions) đều update cùng table → no duplicate.

### 19.3 Window mở rộng cho reminder workflow

Internal scheduler dùng `REMINDER_HALF_WINDOW_MIN = 2` → window 28-32 phút (4 phút wide). Tick mỗi 60s nên cover được.

GitHub Actions cron `*/5` có jitter 5-15p → script `trigger_reminders.py` tự mở rộng window thành 25-35 phút (10p wide):

```python
HALF_WIN = max(scheduler.REMINDER_HALF_WINDOW_MIN, 5)  # ≥ 5 phút each side
lower = now + timedelta(minutes=30 - HALF_WIN)  # 25
upper = now + timedelta(minutes=30 + HALF_WIN)  # 35
```

→ Cover được trường hợp workflow trễ tối đa 5 phút (event 9:00, expected ping 8:30, actual ping 8:35 → window vẫn catch nếu range 25-35).

### 19.4 GitHub Secrets cần setup

Lần đầu deploy Phase 15, cần chị Yến copy các env vars từ Render dashboard sang GitHub repo Settings → Secrets and variables → Actions → New repository secret:

```
TELEGRAM_BOT_TOKEN
TELEGRAM_ALLOWED_CHAT_ID
ZOOM_ACCOUNT_ID
ZOOM_CLIENT_ID
ZOOM_CLIENT_SECRET
GOOGLE_CLIENT_ID
GOOGLE_CLIENT_SECRET
GOOGLE_REFRESH_TOKEN
GOOGLE_CALENDAR_ACCOUNT
TURSO_DATABASE_URL
TURSO_AUTH_TOKEN
CONTACT_NAME
CONTACT_TITLE
```

Sau khi set → 2 workflow tự chạy theo lịch. Lần đầu test có thể bấm `Run workflow` trong tab Actions để verify.

### 19.5 Trade-off

**Lợi:**
- 100% reliable. GitHub Actions cron jitter chấp nhận được vì window được mở rộng.
- Hoạt động ngay cả khi Render bot down hoàn toàn (vd downtime, deploy fail).
- Dễ debug: mỗi workflow run có log đầy đủ trong tab Actions.

**Trade-off:**
- Mỗi run mất ~20s setup (checkout + pip install). Reminders chạy mỗi 5 phút = ~3000 phút/tháng cho mình job này. GitHub Actions free tier có 2000 phút/tháng cho private repo → có thể vượt quota nếu chạy mỗi 5p liên tục.
- **Public repo: free tier UNLIMITED.** Repo `Yennie1702/zoom-calendar-bot` đang private → cần convert public hoặc chấp nhận chi phí thêm (~$0.008/phút beyond 2000).

→ Em đề xuất khi nào sang Phase 15.1 sẽ tối ưu: cache pip install + skip workflow khi không có event sắp tới (đọc DB nhanh ở 1 step trước, exit early). Hiện tại MVP cứ chạy đầy đủ.

---

*Cập nhật cuối: 2026-04-28 — sau Phase 15 (External GitHub Actions triggers).*

---

## 17. Phase 3.0 — `/whoami` public command

**Mục đích:** Pre-work cho Phase 3 multi-user. Cần lấy `user_id` + `chat_id` của Hương + Thuỳ trước khi whitelist họ vào `USERS` config.

**Đặc thù:**
- Public — bypass `_is_allowed` check (mọi user gõ được)
- Audit log: nếu user_id không trong USERS → log với `display_name="UNAUTHORIZED_USER"`, `result="reject"` để admin thấy probe attempts qua `/audit warnings`

**Output:**
```
🆔 Thông tin của bạn:
👤 User ID: <id>
📛 Tên Telegram: <first> <last>
🔗 Username: @<username>
💬 Thông tin chat hiện tại:
📍 Chat ID: <chat_id>
📂 Loại chat: <type>
📋 Tên chat: <title>          (chỉ group/supergroup)
⏱ Thời gian: HH:MM:SS DD/MM/YYYY VN
```

**Implementation:** `bot/handlers.cmd_whoami` — defensive None handling cho `last_name`, `username`, `chat.title`.

---

## 18. Phase 3 — Multi-user architecture

**Mục tiêu:** Mở rộng bot từ 1 user (chị Yến) → 3 user (chị Yến + Hương + Thuỳ) trong group "JA Scheduler Team", giữ nguyên 100% behavior chế độ Personal hiện tại.

### 18.1 Hai chế độ song song

| Chế độ | Chat | Calendar | Color | Template | Attendees |
|---|---|---|---|---|---|
| **Personal** | 1-1 chị Yến (chat_id=8173041182) | `primary` | default Calendar | "Hải Yến \| John Academy" hardcode | chỉ khách |
| **Group** | "JA Scheduler Team" (chat_id=-5136308743) | `GOOGLE_CALENDAR_TEAM_ID` | theo `USERS[uid].calendar_color` (Hương=9, Thuỳ=10; Yến admin = default) | `USERS[uid].signature` | khách + creator email |

### 18.2 USERS config (`bot/users_config.py`)

Source of truth = file Python commit lên git (3 user, hiếm thay đổi → file đơn giản hơn DB CRUD).

```python
@dataclass(frozen=True)
class UserConfig:
    user_id: int
    display_name: str          # "Hải Yến" / "Quỳnh Hương" / "Vũ Kim Thuỳ"
    email: str
    role: str                   # "admin" | "member"
    team: str                   # "John Academy" / "JoyClub" / "JohnBook"
    calendar_color: str        # eventColorId (1-11)
    title_prefix: str          # "[John Academy] " hoặc ""
    signature: str             # multi-line embed Calendar description
    telegram_username: str = ""  # cho mention reminder (vd "QuynhHuongNgo")
```

3 entries: Hải Yến (admin/JA/cam), Quỳnh Hương (member/JoyClub/xanh biển), Vũ Kim Thuỳ (member/JohnBook/xanh lá).

### 18.3 Permission gate (`bot/permissions.py`)

`resolve_context(update) → RequestContext`:
- chat_id == OWNER_USER_ID && user_id == OWNER_USER_ID → mode='personal'
- chat_id == GROUP_CHAT_ID && user_id ∈ USERS → mode='group'
- chat_id == GROUP_CHAT_ID && user_id ∉ USERS → mode='reject' với "chưa được cấp quyền"
- Khác → mode='reject' với "chat không được phép"

`can_modify_event(ctx, row)`:
- Cross-mode (personal user thao tác group row, vice versa) → reject
- Personal mode → full quyền
- Group + admin → full quyền với group rows
- Group + member → chỉ row mình tạo (`row.created_by_user_id == ctx.user_id`)

`audit(ctx, command, params, result, error_message)`: wrap `db.log_audit` với context auto-fill.

---

## 19. Phase 3 — DB schema + migration

### 19.1 Schema additions

**Bảng `events`** thêm 3 cột (idempotent ALTER với try/except):
```sql
created_by_user_id INTEGER          -- Telegram user_id người tạo
created_by_display_name TEXT        -- snapshot tên tại lúc tạo
chat_mode TEXT                      -- "personal" | "group"
```

**Bảng `audit_log`** mới (CREATE IF NOT EXISTS + 2 index):
```sql
CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,        -- ISO 8601 UTC
    user_id INTEGER,
    display_name TEXT,
    chat_mode TEXT,                 -- "personal" | "group" | "reject" | "unknown"
    command TEXT,
    params TEXT,                    -- JSON-stringified, max 500 chars
    result TEXT,                    -- "success" | "fail" | "reject"
    error_message TEXT
);
CREATE INDEX ix_audit_timestamp ON audit_log (timestamp DESC);
CREATE INDEX ix_audit_user_id ON audit_log (user_id);
```

### 19.2 Migration backfill (`scripts/migrate_phase3.py`)

Idempotent — chỉ UPDATE rows có `created_by_user_id IS NULL`:
```sql
UPDATE events
SET created_by_user_id = 8173041182,
    created_by_display_name = 'Hải Yến',
    chat_mode = 'personal'
WHERE created_by_user_id IS NULL;
```

Chạy thủ công local sau commit code: `venv/bin/python scripts/migrate_phase3.py --yes`.

### 19.3 NULL fallback trong query

`list_recent / search_events / count_events / events_on_date` thêm filter optional `chat_mode` + `created_by_user_id`. WHERE clause với NULL fallback (cho rows cũ chưa backfill):

```sql
(chat_mode = ? OR (chat_mode IS NULL AND ? = 'personal'))
(created_by_user_id = ? OR (created_by_user_id IS NULL AND ? = 8173041182))
```

→ Trước khi backfill, lịch cũ vẫn truy xuất được như personal/Yến.

### 19.4 Rename display_name (2026-04-29 hot-fix)

Sau khi deploy Phase 3 ban đầu, chị Yến yêu cầu đổi tên:
- "Hương" → "Quỳnh Hương"
- "Thuỳ" → "Vũ Kim Thuỳ"

`scripts/migrate_display_names.py` backfill `events.created_by_display_name` + `audit_log.display_name` cho rows cũ. Idempotent — filter `WHERE display_name = old_name`.

---

## 20. Phase 3 — Permission matrix + chat_mode flow

### 20.1 Permission matrix

| Hành động | Personal | Group-Member | Group-Admin |
|---|:-:|:-:|:-:|
| `/whoami` | ✅ | ✅ | ✅ |
| Tạo lịch | ✅ | ✅ | ✅ |
| `/mylist` | ✅ | ✅ | ✅ |
| `/list` (all) | ✅ | ❌ "dùng /mylist" | ✅ |
| `/list_users` | ✅ | ❌ admin only | ✅ |
| Sửa/xoá lịch mình | ✅ | ✅ | ✅ |
| Sửa/xoá lịch người khác | N/A | ❌ | ✅ |
| `/sync ID` | ✅ | ✅ (lịch mình) | ✅ (all) |
| `/audit` | ✅ | ❌ | ✅ |
| `/members` (read) | ✅ | ✅ | ✅ |
| `/members add/rm` | ✅ | ❌ admin only | ✅ |
| `HY` mode | ✅ | ❌ "chỉ personal" | ❌ |

### 20.2 Create flow theo mode

`_do_create` branch theo `req_mode`:

```
if req_mode == "group" and creator_cfg:
    target_cal = CALENDAR_TEAM_ID()
    target_color = creator_cfg.calendar_color if creator_cfg.role == "member" else None
    # Yến (admin) trong group: dùng default Calendar TEAM (chị set màu sẵn)
    # Hương/Thuỳ (member): override color (xanh biển/xanh lá)
    title_prefix = creator_cfg.title_prefix
    final_attendees = khách + creator.email   # Kịch bản B
    description = format_group_calendar_description(...)  # signature từ USERS
else:  # personal
    target_cal = None  # primary
    target_color = None  # default Calendar primary
    title_prefix = "[John Academy] "
    final_attendees = khách
    description = format_calendar_description(...)  # template cũ
```

Insert event với attribution:
```python
db.insert_event(
    ...
    created_by_user_id=req.user_id,
    created_by_display_name=req.display_name,
    chat_mode=req.mode,
)
```

### 20.3 calendar_id propagation cho mọi Calendar API call

**Bug fix critical** (Phase 3 commit `8d68112`): mọi API call sau insert (delete/edit/sync/occurrence) phải truyền `calendar_id` đúng theo row.chat_mode.

Helper `_calendar_id_for_row(row) → str`:
- `chat_mode='group'` → `CALENDAR_TEAM_ID()`
- `chat_mode='personal'` (or NULL) → `CALENDAR_PERSONAL_ID()` (= primary)

Wire vào 6 nơi:
- `compute_drift` → `get_event`
- `_fetch_occurrences` → `list_instances`
- `_apply_edit` → `patch_event`
- `_do_delete` → `delete_event`
- `_apply_occurrence_edit` → `patch_instance`
- `_do_delete_occurrence` → `cancel_instance`

**Trước fix**: API mặc định calendarId=primary → event ở Calendar TEAM không bị xoá thật, chỉ remove chị Yến khỏi attendees (organizer = Calendar TEAM). Symptom: chị Yến phản ánh "lịch xoá nhưng vẫn hiện trên Calendar TEAM, attendees còn lan@abc.com".

---

## 21. Phase 3 — Reply confirm gộp creator info

### 21.1 Vấn đề ban đầu

Phase 3 commit #4 thiết kế `bot/group_notify.py` gửi tin riêng vào group chat khi có create/edit/delete/sync trong group mode. Kết quả: **2 tin cho mỗi action** (reply confirm + notify riêng) → spam group, content trùng lặp.

Chị Yến phản ánh + yêu cầu **gộp 2 thành 1**.

### 21.2 Giải pháp — augment reply confirm

Trong group mode, inject creator/actor info vào reply confirm thay vì gửi tin notify riêng.

**Create reply** (group mode):
```
✅ Đã tạo xong: *Tư vấn JoyClub*
👤 *Người tạo:* Quỳnh Hương (JoyClub)

📅 Thứ 5, 30/4/2026, 14:00 - 15:00
⏱ 60 phút/buổi
🔗 Link Zoom: ...
📧 Đã mời khách:
  • lan@abc.com
  • ngoquynhhuong@john.vn
🆔 DB id: 26 — gõ /mylist để sửa/xoá.
```

**Edit reply** (group mode):
```
✅ Đã cập nhật. Khách sẽ nhận email cập nhật.
👤 *Sửa bởi:* Quỳnh Hương (JoyClub)
✏️ 🕐 Giờ/ngày: 14:00 30/04/2026 → 15:30 30/04/2026

[format_event_detail(row) đầy đủ]
```

**Delete reply** (group mode):
```
🗑 Đã xoá lịch *Tư vấn JoyClub* (id=26). Khách đã nhận email huỷ.
👤 *Xoá bởi:* Quỳnh Hương (JoyClub)
⏰ Thời gian: 14:00 30/04/2026 (60 phút)
```

**Sync reply** (group mode):
```
✅ Đã đồng bộ.
👤 *Sửa bởi:* Hải Yến (John Academy)
🔄 Thay đổi (Calendar → Bot + Zoom):
  • Giờ: 14:00 → 15:00

[format_event_detail(updated)]
```

### 21.3 Filter spec

Notify cho group reply chỉ inject actor + diff khi field thuộc:
- `time` (giờ/ngày)
- `dur` (thời lượng)
- `topic` (tên lịch)
- `att_add` / `att_rm` (thêm/bỏ khách)

**Skip cho `ag` (agenda/nội dung)** — Q1.A spec: nội dung mô tả ít quan trọng, không cần broadcast.

**Skip cho `/sync` no-diff**: nếu drift apply không có thay đổi quan trọng (chỉ agenda hoặc empty diffs) → reply confirm không inject diff block.

### 21.4 Personal mode KHÔNG đổi

Reply confirm cho personal mode giữ nguyên format cũ (không có dòng "Người tạo: ..."). Detect qua `req_mode == "group"` trước khi inject.

`bot/group_notify.py` giữ trong code base (dead code dự phòng cho future use, vd reminder broadcast riêng), nhưng KHÔNG được wire vào handlers.

### 21.5 Hint command theo mode

Tin reply confirm cuối có dòng `🆔 DB id: X — gõ {cmd} để sửa/xoá.`:
- Personal mode → `/list`
- Group mode → `/mylist` (vì `/list` admin only, member click bị reject)

---

## 22. Phase 3 — `/mylist` + `/list_users` + `/audit`

### 22.1 `/mylist` (member-friendly)

Filter `chat_mode=req.mode AND created_by_user_id=req.user_id` → chỉ lịch của chính người gõ.

**Bypass legacy `format_list` path** (commit `37d311c` privacy fix): render header + summary trực tiếp:
```
📋 *Lịch của Quỳnh Hương* (chế độ group, N lịch):
1. 🎯 30/4 14:00 · Tư vấn JoyClub
...
```

Empty state:
```
📭 Quỳnh Hương chưa tạo lịch nào trong chế độ group.
```

### 22.2 `/list` (admin-only trong group)

- Personal mode → tất cả lịch personal của Yến (như cũ)
- Group + admin (Yến) → tất cả lịch group, có pagination + filter
- Group + member → reject với hint "Bạn chỉ xem được /mylist..."

### 22.3 `/list_users` (admin-only)

Hiện 3 users từ `USERS` dict format theo spec:
```
👥 Danh sách user trong hệ thống (3 người):

1. Hải Yến (Admin)
   Team: John Academy
   User ID: 8173041182
   Email: nguyenthihaiyen@john.vn
...
```

### 22.4 `/audit` (admin-only)

4 sub-commands:
- `/audit` — 20 log gần nhất
- `/audit today` — log ngày VN hôm nay (date_from = date_to = today)
- `/audit user <name>` — match `display_name` partial (case-insensitive) → user_id
- `/audit errors` (alias `warnings`, `reject`) — `result IN ('fail','reject')`

Output compact line: `<emoji> <ts> <name> [<mode>] <command> <params...> — <error>`. Icons: ✅ success, ❌ fail, ⛔ reject. Truncate output > 3900 chars (Telegram 4096 limit).

---

## 23. Phase 3 — Setup checklist (Render + GH + Drive)

### 23.1 Render env vars (chị Yến set qua dashboard)

```
TELEGRAM_OWNER_USER_ID         = 8173041182
TELEGRAM_GROUP_CHAT_ID         = -5136308743
GOOGLE_CALENDAR_PERSONAL_ID    = primary
GOOGLE_CALENDAR_TEAM_ID        = c_85a9e82f...@group.calendar.google.com
```

`TELEGRAM_ALLOWED_CHAT_ID` giữ nguyên `8173041182` (legacy fallback).

### 23.2 GitHub Secrets bổ sung (cho reminder workflow)

```bash
gh secret set TELEGRAM_OWNER_USER_ID --body "8173041182"
gh secret set TELEGRAM_GROUP_CHAT_ID --body "-5136308743"
```

### 23.3 Telegram setup

1. **@BotFather** → `/mybots` → JA Scheduler bot → Bot Settings → **Group Privacy** → **Turn off** (mặc định bot trong group chỉ thấy commands; tắt để bot thấy text "Tạo lịch ...")
2. **Kick bot khỏi group + add lại** sau khi tắt privacy (Telegram cache permission cũ trên server side, kick + add mới refresh)
3. (Optional) Promote bot làm admin với quyền "Pin messages" để auto-pin tin help/intro

### 23.4 Google Calendar TEAM setup

1. Tạo Calendar mới tên "JA Scheduler Team" trên Google Calendar UI
2. Lấy Calendar ID từ Settings → Integrate calendar → "Calendar ID"
3. Set vào env `GOOGLE_CALENDAR_TEAM_ID`
4. Share với Hương (`ngoquynhhuong@john.vn`) + Thuỳ (`vukimthuy@john.vn`) với quyền **"Make changes to events"**
5. (Optional) Set default color cho Calendar TEAM trong UI — chị Yến (admin) tạo lịch sẽ dùng màu này

### 23.5 Migration (chạy local 1 lần sau deploy)

```bash
venv/bin/python scripts/migrate_phase3.py --yes
```

Backfill `created_by_user_id=8173041182, chat_mode='personal'` cho lịch cũ. Idempotent.

### 23.6 Test checklist

Đầy đủ trong `docs/team-onboarding.md` (3 bài cho member: /whoami → tạo lịch test → sửa/xoá test). File `.docx` đã upload Drive folder + share view link cho team không cần GitHub access.

---

## 24. Phase 3 — Bug fixes hậu deploy

Sau khi deploy Phase 3 commits #1-6, em đã hot-fix 7 bug critical:

| SHA | Bug | Fix |
|---|---|---|
| `a0aa71a` | Personal mode Yến enforce color="6" → khác màu Calendar primary cũ chị đã set | Bỏ enforce → `color_id=None` |
| `186ba5b` | `handle_callback` chưa migrate Phase 3 → mọi click button trong group bị silent-drop | Switch `_is_allowed` → `resolve_context` + audit reject |
| `189e768` → `8d68112` | (a) notify_* gửi tin riêng → duplicate với reply confirm; (b) Calendar API call sau create không truyền `calendar_id` → delete/edit lịch group bị treat thành "decline" thay vì xoá thật | (a) Gộp creator info vào reply, bỏ `notify_*` wiring; (b) Helper `_calendar_id_for_row` propagate calendar_id đúng cho 6 API calls |
| `099059d` | Yến (admin) trong group enforce color="6" Tangerine → khác màu Calendar TEAM default | Chỉ enforce color cho `role=='member'` (Hương/Thuỳ); admin dùng default Calendar |
| `adb2628` | Display name spec ban đầu "Hương"/"Thuỳ" — chị Yến muốn đầy đủ | Update USERS + signature; `migrate_display_names.py` backfill rows cũ |
| `37d311c` | **CRITICAL privacy leak** — Hương gõ `/mylist` group → bot trả 6 lịch personal của Yến (gồm HY private). Local test với filter trả 0 rows nhưng Render trả 6 → có thể format_list legacy path bind filter sai | Bypass legacy format → render header + summary inline trong `cmd_mylist`. Add log để Render trace. Empty state explicit |
| `1f1a7de` | Reply confirm tạo lịch group hint "/list" → Hương click bị reject (admin only) | Hint dynamic theo `req_mode`: group → `/mylist`, personal → `/list` |

### 24.1 Telegram privacy mode gotcha

Mặc định bot Telegram có **privacy mode ON** trong group → bot chỉ thấy:
- Commands (`/list`, `/whoami`...)
- Reply tới message của bot
- @mentions tag bot

Text thường (vd "Tạo lịch ...") **bot không thấy** trong group khi privacy mode ON.

**Fix**: tắt qua @BotFather → kick bot + add lại để refresh permission cache.

Nếu thêm member mới vào group sau khi tắt privacy → có thể vẫn cache cũ cho member đó. Re-test bằng `/whoami`. Nếu silent → kick bot ra + add lại.

### 24.2 Sandbox restrictions phát hiện

- Bulk `gh secret set` từ `.env` bị Claude sandbox block (credential exfiltration check) → user phải chạy local
- Direct UPDATE Turso production khi đang debug bug bị block → write SQL script cho user paste Terminal local
- SSH probe github.com bị block → user tự chạy `git remote set-url ... && git push`

→ Pattern: viết script + commit, user chạy local. Em document vào `reference_github_actions_keep_alive.md` memory để tránh lặp lại.

---

## 25. Phase history (cập nhật cuối)

| Phase | Date | Mô tả |
|---|---|---|
| 0-15 | 2026-04-21 → 2026-04-28 | Xem bảng cũ ở Section 12 + Phase 11/12/13/13.1/14/14.1/15 ở các Section trên |
| 3.0 | 2026-04-28 | `/whoami` public command — pre-work multi-user (commit `432c4c8`) |
| 3 (#1-6) | 2026-04-28 | Multi-user core: schema + permissions + chat_mode flow + group_notify (sau gộp vào reply) + /mylist /list_users /audit + reminder multi-target |
| 3 hot-fixes | 2026-04-28 → 2026-04-29 | 7 bug fix sau deploy (xem Section 24) |
| Display rename | 2026-04-29 | "Hương" → "Quỳnh Hương", "Thuỳ" → "Vũ Kim Thuỳ" + backfill |
| Privacy fix | 2026-04-29 | `/mylist` leak fix + hint `/list` → `/mylist` trong group reply confirm |
| Team docs | 2026-04-29 | `docs/team-onboarding.md` + `team-onboarding.docx` upload Drive + intro message gửi group |

---

*Cập nhật cuối: 2026-04-29 — sau Phase 3 multi-user complete + 7 hot-fixes.*
