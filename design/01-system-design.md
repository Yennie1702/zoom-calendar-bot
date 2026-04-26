# JA Scheduler Bot — Tài liệu Thiết kế Hệ thống

**Phiên bản:** 1.0 (2026-04-26)
**Owner:** chị Hải Yến (nguyenthihaiyen@john.vn)
**Repo:** https://github.com/Yennie1702/zoom-calendar-bot
**Trạng thái:** Production · Deploy trên Render Free tier

---

## 1. Tổng quan & mục tiêu

JA Scheduler Bot là Telegram bot single-user giúp chị Hải Yến (John Academy) tự động:
- Tạo Zoom meeting + Google Calendar event + gửi invite cho khách bằng 1 tin nhắn tiếng Việt.
- Quản lý (xem · sửa · xoá · tìm · lọc) toàn bộ lịch qua chat.
- Tách nhánh **lịch HY cá nhân** (Google Meet thay Zoom, visibility=private — sếp/đồng nghiệp chỉ thấy busy-block).
- Nhắc tự động 30 phút trước mỗi buổi + digest 7h sáng mỗi ngày.
- Đồng bộ 2 chiều với Google Calendar (đọc external events + sync drag-drop từ Calendar UI).

**Phi mục tiêu:**
- Multi-user (chỉ duy nhất 1 chat_id whitelisted).
- Real-time chat — thiết kế cho async, polling-based.
- Mobile-native UI — Telegram là interface duy nhất.

---

## 2. Stakeholders & ràng buộc

| Stakeholder | Vai trò | Ảnh hưởng thiết kế |
|---|---|---|
| Chị Hải Yến | User duy nhất, owner | UI tiếng Việt, single-chat whitelist, free-tier hosting |
| Khách của chị | Người nhận invite | Email Calendar invite, Zoom join link, không tương tác bot |
| Sếp Đạt + đồng nghiệp | Người xem Calendar chung | Lịch HY phải private (chỉ thấy busy-block) |
| John Academy | Tổ chức | Branding "John Academy" trên invite work, ẩn cho lịch HY |

**Ràng buộc hệ thống:**
- Render Free tier: container sleep sau 15 phút idle → cần keep-alive 5-10 phút để giữ scheduler loop sống.
- Telegram sendMessage giới hạn 4096 chars/tin → help/digest dài phải chia 2 tin.
- Turso free tier: limited storage + bandwidth → minimal schema.
- Render container chạy UTC → phải convert sang Asia/Ho_Chi_Minh khi compare time.

---

## 3. Kiến trúc tổng thể

```
                                    ┌──────────────────────┐
                                    │   Chị Hải Yến         │
                                    │   (Telegram chat)     │
                                    └──────────┬────────────┘
                                               │ message / button click
                                               ▼
                          ┌────────────────────────────────────┐
                          │       Telegram Bot API             │
                          │       (webhook → bot prod)         │
                          └────────────────┬───────────────────┘
                                           │ POST /telegram
                                           ▼
   ┌────────────────────────────────────────────────────────────────────────┐
   │  Render Web Service (ja-scheduler-bot.onrender.com)                    │
   │  Python 3.12 · python-telegram-bot[webhooks] · single asyncio loop     │
   │                                                                        │
   │  ┌───────────────┐  ┌───────────────┐  ┌─────────────────────────┐   │
   │  │  handlers.py  │──│   parser.py   │  │  scheduler.py           │   │
   │  │  (commands +  │  │ (tiếng Việt → │  │  (loops asyncio:        │   │
   │  │   callbacks)  │  │ ParsedCommand)│  │    30p reminder +       │   │
   │  └───────┬───────┘  └───────────────┘  │    07:00 digest)        │   │
   │          │                              └─────────────────────────┘   │
   │          ├───────► db.py ──────► Turso libSQL (events, bot_meta...)   │
   │          ├───────► zoom_client.py ──► Zoom API (S2S OAuth)            │
   │          ├───────► calendar_client.py + external_events.py            │
   │          │            └─► Google Calendar API v3 + Meet auto-gen      │
   │          └───────► formatter.py (Markdown/HTML output)                │
   └────────────────────────────────────────────────────────────────────────┘
                                           │
                                           ▼ (write)
        ┌──────────────────────────────────────────────────────────────┐
        │  Turso libSQL DB (cloud SQLite, free tier)                   │
        │  - events (lịch bot tạo + meta + reminders_sent)             │
        │  - bot_meta (key/value: last_digest_date)                    │
        │  - external_reminders_sent (dedupe lịch ngoài bot)           │
        └──────────────────────────────────────────────────────────────┘

   ┌────────────────────────────────────────────────────────────────────────┐
   │  External integrations                                                  │
   │  - Telegram Bot API (webhook + sendMessage)                            │
   │  - Zoom REST API v2 (S2S OAuth) — create/update/delete meetings        │
   │  - Google Calendar API v3 (OAuth refresh token) — events + Meet conf  │
   │  - Google Drive API v3 (drive.file scope) — daily backup upload       │
   └────────────────────────────────────────────────────────────────────────┘

   ┌────────────────────────────────────────────────────────────────────────┐
   │  Off-Render automation                                                  │
   │  - GitHub Actions keep-alive (cron 5p) → ping / để Render không sleep │
   │  - macOS launchd daily 23h → backup_all.sh:                           │
   │    1. backup_db.py     (Turso → data/backups/db_<TS>.sql.gz)         │
   │    2. backup_memory.sh (~/.claude memory → data/memory_backups/)      │
   │    3. backup_to_drive.py (tar data/+bot/ → Google Drive)              │
   └────────────────────────────────────────────────────────────────────────┘
```

---

## 4. Tech stack

| Tầng | Technology | Lý do chọn |
|---|---|---|
| Runtime | Python 3.12 (Render) / 3.14 (local dev) | Stable, async/await native |
| Bot framework | python-telegram-bot v20+ (`extras=[webhooks]`) | Best-in-class PTB lib, async-first |
| Webhook server | Built-in tornado (qua PTB extras) | Không cần extra Flask/FastAPI |
| DB | Turso libSQL (cloud) + sqlite3 fallback (local) | SQLite syntax familiar, free tier đủ |
| DB client | `libsql-client` (HTTP) / `sqlite3` stdlib | Pure Python, không cần native deps |
| Calendar | `google-auth` + `google-api-python-client` | Official Google SDK |
| Zoom | `requests` + S2S OAuth | REST simple, scope `meeting:write:meeting` |
| Hosting | Render Web Service Free tier (Singapore) | 0đ, gần VN, auto-deploy GitHub |
| Persistence | Turso DB (free tier 9GB) | Cloud SQLite, không cần file system Render |
| Drive backup | `google-api-python-client` Drive v3 | Reuse OAuth flow đã có |
| Keep-alive | GitHub Actions cron */5 * * * * | 0đ, không phụ thuộc Render uptime |
| Local backup cron | macOS launchd | Native, không cần external service |

---

## 5. Components

### 5.1 `bot/main.py` — Entry point

Quyết định runtime mode:
- Có `$PORT` env (Render) → webhook mode (`run_webhook` listen 0.0.0.0:$PORT, path `/telegram`).
- Không có `$PORT` (local dev) → polling mode (`run_polling`).

Đăng ký 5 command handlers (`/start /help /list /sync /today`), 1 message handler, 1 callback handler. Kích hoạt scheduler qua `post_init`.

### 5.2 `bot/handlers.py` — Telegram command + callback dispatch

File lớn nhất (~2300 dòng) chứa:
- 5 command handlers (`cmd_start, cmd_help, cmd_list, cmd_sync, cmd_today`).
- `handle_text` — phân loại tin nhắn theo intent (tạo lịch, edit nhanh, clone, HY, huỷ).
- `handle_callback` — xử lý ~20 loại button callback (`confirm_create, ed_menu, ed_f, del_confirm, occ_pick, ext_*, ...`).
- Helper functions:
  - `_do_create` / `_do_create_personal` / `_do_clone` — write path
  - `_apply_edit` / `_do_delete` / `_apply_occurrence_edit` / `_do_delete_occurrence` — modify path
  - `_apply_drift` — sync from Calendar UI changes
  - `_fetch_occurrences` — explode recurring → list

Pattern: mỗi action có 2-step flow (preview → confirm) với `ctx.chat_data` lưu pending state.

### 5.3 `bot/parser.py` — Tiếng Việt NLP

Pure regex parser, output `ParsedCommand` dataclass. 3 entry points:
- `parse_create()` — `Tạo lịch "..."` hoặc `HY "..."` (cá nhân).
- `parse_edit_quick()` — `sửa giờ 15h #5`, `xoá lịch khách lan@abc.com ngày mai`.
- `parse_clone()` — `tạo lịch giống #5 nhưng ngày 27/4 15h`.
- `parse_list_args()` — `/list tuần này`, `/list khách lan@abc.com`, etc.

Vietnamese-specific:
- "thứ 2/3/4..." → `BYDAY=MO/TU/WE...`
- "8h30 sáng thứ 4 hàng tuần trong 12 tuần liên tiếp bắt đầu từ 20/5/2026" → recurring spec
- "tuần này / mai / hôm qua / tháng 5/2026 / 27/4-4/5" → date ranges

`ParsedCommand` flag `is_personal=True` cho prefix HY → bot route sang `_do_create_personal`.

### 5.4 `bot/db.py` — Storage adapter

Hỗ trợ 2 backend:
- **Turso libSQL** (production): khi `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` có.
- **SQLite local** (dev): file `data/events.db`.

Lớp `_TursoConn` adapter expose API giống `sqlite3`:
```python
conn.execute(sql, params).fetchone()
conn.commit()
conn.lastrowid
```
Để code business logic dùng chung 2 backend.

3 bảng:
- `events` — lịch bot tạo (15 cột chính + `cancelled_occurrences` JSON + `reminders_sent` JSON + `provider` + `meet_join_url`).
- `bot_meta` — key/value (key=`last_digest_date`).
- `external_reminders_sent` — dedupe reminder cho lịch không do bot tạo.

### 5.5 `bot/scheduler.py` — Background loops

Hai asyncio tasks chạy trong cùng loop với webhook listener (không thread):
- **`reminder_loop`**: tick mỗi 60s, query lịch trong window 28-32 phút tới, gửi reminder, mark `reminders_sent`. Cover cả lịch DB lẫn external.
- **`daily_digest_loop`**: tick mỗi 60s, fire khi `7h ≤ giờ < 19h` và `bot_meta.last_digest_date != today`. Đồng thời gửi digest list lịch hôm nay (sort theo giờ, icon 🎯/🔁/📅/🔒).

`_now_vn()` helper convert UTC (Render) → Asia/Ho_Chi_Minh naive (matches DB format) — fix tz bug Phase 8.

### 5.6 `bot/calendar_client.py` + `bot/external_events.py`

- **`calendar_client.py`**: Google Calendar API v3 wrapper — OAuth user flow (refresh token). Methods: `create_event`, `update_event`, `delete_event`, `patch_description` (cho HY 2-step flow), `add_attendees`.
- **`external_events.py`**: Đọc lịch chị Yến tự tạo trên Calendar (không qua bot). Normalize raw API response → `ExternalOccurrence` dataclass. Dedupe với DB qua `calendar_event_id` + `recurringEventId`. Đặc biệt xử lý timezone (RFC3339 với offset → naive VN).

### 5.7 `bot/zoom_client.py` — Zoom API

S2S OAuth flow (account-level credentials), tạo/update/delete meeting. Recurring meeting support (Type=8, weekly).

### 5.8 `bot/drive_client.py` — Google Drive (cho backup)

Reuse cùng OAuth refresh token với Calendar. Scope `drive.file` (chỉ thấy file bot tự tạo). Methods:
- `ensure_folder(name, parent_id)` — tạo nếu chưa có
- `upload_file(local, drive_name, parent_id)` — resumable upload
- `list_files_in_folder` + `delete_file` — cho retention cleanup

### 5.9 `bot/formatter.py` — Output rendering

Format Markdown/HTML cho Telegram:
- `format_calendar_description` (work) + `format_personal_calendar_description` (HY)
- `format_confirm_preview`, `format_event_detail`, `format_list`, `format_digest` (qua scheduler)
- `format_event_summary` — 1-line label cho /list buttons
- `format_conflict_warning` — banner overlap

### 5.10 `bot/config.py` — Env loader

Load 14 env vars qua dotenv. Constants: `TIMEZONE = "Asia/Ho_Chi_Minh"`. Helper `google_ready()`.

---

## 6. Data Model

### 6.1 `events` table

```sql
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,            -- ISO UTC
    updated_at TEXT NOT NULL,            -- ISO UTC
    topic TEXT NOT NULL,                 -- "Tư vấn OKRs - Chị Lan"
    start_local TEXT NOT NULL,           -- "2026-04-22T14:00:00" (naive VN)
    duration_min INTEGER NOT NULL,
    agenda TEXT NOT NULL DEFAULT '',
    attendees TEXT NOT NULL,             -- JSON array of emails
    recurring TEXT,                      -- JSON {"byday": "WE", "count": 12} or NULL
    zoom_meeting_id TEXT NOT NULL,       -- empty for HY (provider=meet)
    zoom_join_url TEXT NOT NULL,
    zoom_passcode TEXT NOT NULL DEFAULT '',
    calendar_event_id TEXT NOT NULL,
    calendar_event_link TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'active', -- active | deleted
    cancelled_occurrences TEXT DEFAULT '[]', -- JSON array of occurrence ISO starts
    reminders_sent TEXT DEFAULT '[]',    -- JSON array (dedupe)
    provider TEXT NOT NULL DEFAULT 'zoom', -- 'zoom' | 'meet'
    meet_join_url TEXT NOT NULL DEFAULT '' -- non-empty when provider=meet
);
```

### 6.2 `bot_meta` table

```sql
CREATE TABLE bot_meta (key TEXT PRIMARY KEY, value TEXT);
-- Hiện tại chỉ 1 key: 'last_digest_date' = '2026-04-26' (ngày cuối digest fire)
```

### 6.3 `external_reminders_sent` table

```sql
CREATE TABLE external_reminders_sent (
    calendar_event_id TEXT NOT NULL,
    occurrence_iso TEXT NOT NULL,
    sent_at TEXT NOT NULL,
    PRIMARY KEY (calendar_event_id, occurrence_iso)
);
```

### 6.4 Recurring representation

`recurring` JSON column:
```json
{"byday": "WE", "count": 12}
```
Bot expand thành 12 occurrences khi cần (reminder, digest, /list filter):
```python
def _expand_event_starts(row):
    if not row.recurring: return [row.start_dt]
    return [row.start_dt + timedelta(weeks=i) for i in range(row.recurring["count"])]
```

`cancelled_occurrences` JSON array các ISO start đã huỷ (skip khi expand).

---

## 7. Data flow — Lifecycle 1 lịch

### 7.1 Create one-time work meeting

```
Chị Yến: 'Tạo lịch "Tư vấn OKRs":\n- Thời gian: ...\n- Khách: ...'
   │
   ▼ Telegram → webhook → handlers.handle_text
   │
   ▼ parser.parse_create → ParsedCommand(is_personal=False, recurring=None, ...)
   │
   ▼ formatter.format_confirm_preview + format_conflict_warning → "📋 Em hiểu lệnh..."
   │
   ▼ Bot send preview + 2 button [✅ Xác nhận tạo / ❌ Huỷ]
   │
   ▼ User bấm "✅ Xác nhận tạo" → handlers.handle_callback → _do_create
   │
   ├─ zoom_client.create_meeting() ─────► Zoom API → meeting_id, join_url, passcode
   ├─ calendar_client.create_event() ───► Google Calendar API → event_id, html_link
   │       (description = format_calendar_description chứa Zoom info)
   ├─ db.insert_event(...) ─────────────► Turso (status=active)
   └─ formatter.format_success_reply ───► Bot reply "✅ Đã tạo xong:..."
```

### 7.2 Create HY personal (Meet, private)

Khác work flow ở 4 điểm:
1. `parser` detect prefix `HY` → `is_personal=True`.
2. **KHÔNG** gọi `zoom_client.create_meeting()`.
3. `calendar_client.create_event(with_meet=True, visibility="private")` → Google Calendar API thêm `conferenceData.createRequest.conferenceSolutionKey.type=hangoutsMeet` + `visibility=private`.
4. **2-step description**: tạo event với placeholder description → Google trả `hangoutLink` → PATCH description chứa Meet link thật. Step 2 non-fatal nếu fail.

DB row có `provider='meet'`, `zoom_*` empty, `meet_join_url=hangoutLink`.

### 7.3 30-min reminder

```
scheduler.reminder_loop tick (mỗi 60s)
   │
   ▼ now = _now_vn()
   ▼ window = [now+28min, now+32min]
   │
   ├─ db.upcoming_unreminded(window) → list lịch DB chưa nhắc trong window
   │     │
   │     ▼ For each: format_reminder + send_message + db.mark_reminded(id, occ_iso)
   │
   └─ external_events.fetch_in_datetime_window(window) → list external Calendar
         │
         ▼ For each: skip if already in external_reminders_sent
         ▼ format_external_reminder + send_message + mark_external_reminded
```

### 7.4 Sync drag-drop từ Calendar UI

```
Chị kéo event trên Google Calendar UI → start_time đổi
   │
   ▼ Chị gõ: '/sync 5' hoặc '/sync' (latest)
   │
   ▼ handlers.cmd_sync
   ▼ calendar_client.get_event(event_id) → fetched start, attendees, summary
   ▼ Compare với DB row → drift detected
   ▼ Show drift summary + button [🔄 Sync (Calendar→Bot)]
   │
   ▼ User confirm → _apply_drift
   │
   ├─ db.update_event(...) (Calendar = source of truth)
   ├─ zoom_client.update_meeting(start_time, ...) (skip nếu provider=meet)
   └─ formatter.format_success → "✅ Đã sync"
```

---

## 8. External integrations

### 8.1 Telegram Bot API
- Webhook URL: `https://ja-scheduler-bot.onrender.com/telegram`
- Method: `setWebhook` 1 lần khi bot start (qua PTB).
- Single-user whitelist: `TELEGRAM_ALLOWED_CHAT_ID` env var → reject mọi chat khác (silent log).
- Rate limit: 30 msg/sec global, 1 msg/sec per chat → bot rất nhẹ tải, không lo.

### 8.2 Zoom API v2 (S2S OAuth)
- Account-level credentials: `ZOOM_ACCOUNT_ID`, `ZOOM_CLIENT_ID`, `ZOOM_CLIENT_SECRET`.
- Scopes: `meeting:write:meeting`, `meeting:update:meeting`, `meeting:delete:meeting`.
- Recurring meeting type=8, weekly_days theo BYDAY.
- Mỗi meeting return `id` + `join_url` + `password`.

### 8.3 Google Calendar API v3 (OAuth user flow)
- Refresh token user-level (chị Yến's john.vn account).
- Scope: `calendar.events`.
- `events().insert/update/patch/delete` với `sendUpdates=all|none` cho notify toggle.
- `events().list` với `singleEvents=True` cho external read (Phase 5).
- Meet auto-gen: `conferenceData.createRequest` + `conferenceDataVersion=1`.

### 8.4 Google Drive API v3 (Phase: backup)
- Cùng refresh token với Calendar (re-OAuth thêm scope `drive.file`).
- Scope `drive.file` = chỉ thấy/sửa file BOT tự tạo (folder `JA-Scheduler-Backups/`), KHÔNG đọc file khác trong Drive.
- Resumable upload qua `MediaIoBaseUpload` chunksize 1MB.

### 8.5 Turso libSQL
- `TURSO_DATABASE_URL` (libsql://... ws/wss) + `TURSO_AUTH_TOKEN` (JWT).
- Schema: 3 bảng (xem section 6).
- Idempotent ALTER TABLE migrations chạy mỗi `_ensure_schema()` call (ignore "duplicate column" errors).

---

## 9. Deployment topology

### 9.1 Render Web Service (production bot)
- Plan: Free tier, region Singapore.
- Build: `pip install -r requirements.txt`.
- Start: `python -m bot.main`.
- Env vars: 14 secrets (Telegram + Zoom + Google + Turso + branding) → set qua Render dashboard.
- Auto-deploy on push to `main` branch GitHub.

**Free tier limits:**
- Container sleep sau 15 phút idle inbound HTTP.
- Cold start ~30s (load Python + connect Turso).
- 750 hours/month free → đủ 24/7 (744h/month).

### 9.2 GitHub Actions keep-alive
- File: `.github/workflows/keep-alive.yml`.
- Cron: `*/10` (hiện tại) hoặc `*/5` (đề xuất). Curl `https://ja-scheduler-bot.onrender.com/`.
- Purpose: ping mỗi N phút để service không sleep → scheduler loop sống → reminder/digest fire đúng giờ.

### 9.3 Local dev (chị Yến's Mac)
- Polling mode khi `$PORT` không set.
- Local SQLite `data/events.db` (không Turso).
- Test command: `venv/bin/python -m bot.main`.

### 9.4 Backup pipeline (launchd daily 23h)
- File: `~/Library/LaunchAgents/com.johnacademy.zoom-calendar-bot.backup.plist`.
- Run `scripts/backup_all.sh`:
  1. `backup_db.py` → `data/backups/db_<TS>.sql.gz` (~3KB)
  2. `backup_memory.sh` → `data/memory_backups/<TS>/*.md` (~40KB)
  3. `backup_to_drive.py` → tar 2 folder + upload Drive (~80KB)
- Retention: 90 ngày (cả local + Drive).
- Idempotent: chạy nhiều lần OK (timestamp khác nhau).

---

## 10. Security & Privacy

### 10.1 Authentication & Authorization
- **Telegram**: chỉ 1 chat_id whitelist (`TELEGRAM_ALLOWED_CHAT_ID`). Mọi chat khác → `_reject` (log only, không reply).
- **Google**: OAuth refresh token user-level (không service account).
- **Zoom**: S2S OAuth account-level (không user OAuth).
- **Drive**: scope `drive.file` minimal (không full Drive access).

### 10.2 Sensitive data
- DB chứa: email khách, Zoom passcode, Calendar links nội bộ.
- Backup local + Drive: cùng dữ liệu, trong tầm tin cậy của chị (Drive private, ổ Space riêng).
- Hiện tại CHƯA encrypt at-rest. Nếu cần (compliance), thêm `age` encrypt cho DB dump.

### 10.3 Secret rotation
- Refresh token Google: rotate khi consent screen update scope (xem `feedback_secret_rotation.md`).
- Zoom/Telegram tokens: zero-downtime rotate được (update env + restart Render).
- Turso auth token: rotate qua Turso CLI.

---

## 11. Observability

### 11.1 Logs
- Render dashboard logs (ephemeral, ~7 ngày).
- Structured logging qua `logging.getLogger(__name__)` — `INFO` mặc định.
- Local `logs/backup.log` cho backup pipeline (persistent trên ổ Space).

### 11.2 Health check
- `GET /` → 404 (PTB không định nghĩa root path) — vẫn dùng được làm keep-alive ping vì >0 = alive.
- `GET /telegram` → 405 (Method Not Allowed) — POST only.

### 11.3 Failure modes & dedup
- Reminder gửi 2 lần: chống bằng `events.reminders_sent` JSON array.
- Digest gửi 2 lần: chống bằng `bot_meta.last_digest_date`.
- External reminder: chống bằng `external_reminders_sent` table.
- Telegram send fail (network, rate limit): `try/except TelegramError` log nhưng không retry — tick tiếp theo sẽ thử lại.

---

## 12. Known limitations & roadmap

### 12.1 Đã biết (Phase 4 chưa làm)
- **Bulk operations**: chưa hỗ trợ "huỷ hết lịch tuần sau" hoặc "dời hết lịch thứ 7 sang chủ nhật". Phải làm 1 lệnh/lịch.

### 12.2 Edge cases
- Render cold start: lần đầu sau idle có thể trễ 30s reply.
- Turso eventual consistency: 2 write song song hiếm khi có race (single-user nên gần như không xảy ra).
- Recurring với `cancelled_occurrences > count`: không filter nếu user chỉnh DB tay.

### 12.3 Tương lai
- Multi-account support (nếu cần đồng nghiệp chị dùng).
- Web dashboard read-only (xem nhanh không qua Telegram).
- Encrypt DB backup at-rest (`age` hoặc `gpg`).
- Migrate Render Free → Starter ($7/m) khi cần persistent disk + zero cold start.

---

## 13. References

- Code: `bot/*.py` + `scripts/*.py`
- README: `README.md`
- Backup ops: `scripts/BACKUP.md`
- Use case spec: `ja-scheduler-bot.usecase.json`
- Memory files: `~/.claude/projects/-Volumes-Space-Claude-JOHNSPACE/memory/project_*.md`

---

*Cập nhật cuối: 2026-04-26 — Phase 8 hot fixes (tz bug + help split) + backup pipeline (local + Drive).*
