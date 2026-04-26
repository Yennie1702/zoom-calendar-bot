---
name: JA Scheduler — Phase 1-3 feature expansion (2026-04-23)
description: Natural targeting, clone, trùng lịch, reminder 30p, digest 7h, external edit qua /list, HY lịch cá nhân Meet-only private. Commits/files/DB schema.
type: project
originSessionId: 7e420da6-5336-46b8-8aa1-d23684ddeb77
---
Deployed 2026-04-23 qua 3 commits trên main (tất cả đã auto-deploy Render):

**Phase 1 — Nhóm A (natural reference + search)** — commit `61b1542`
- `/list tuần này`, `/list tháng 5`, `/list 27/4-4/5` — date-range query
- `/list khách a@x.vn`, `/list mentor` — filter theo khách/keyword topic
- `/list 2` — pagination (10/trang)
- `sửa "Mentor MBOs" ngày 27/4 giờ 15h` — target bằng tên + ngày (không cần #id)
- `xoá khách a@x.vn ngày mai` — target bằng khách
- Disambiguation flow: nhiều match → list numbered để bấm chọn
- Files: `bot/db.py` (+84: search_events, count_events), `bot/parser.py` (+300: TargetSpec, ListQuery, parse_list_args, verb-first restructure), `bot/formatter.py` (+55: pagination), `bot/handlers.py` (+309: list filters, disambig callbacks `lp:`, `qd_sel:`, `qd_cancel`)

**Phase 2 — Nhóm C (smarter creation)** — commit `466ea17`
- `tạo lịch giống #5` — clone lịch cũ thành one-off (recurring source tự drop recurring)
- `tạo lịch giống #5 nhưng ngày 27/4 15h, khách a@x.vn` — clone + override
- `tạo lịch giống "Mentor MBOs" ngày 27/4 nhưng khách a@x.vn` — clone bằng natural target
- Override hỗ trợ: `ngày`/`giờ`/`thời lượng`/`tên "..."`/`nội dung`/`khách ...` (replace) / `thêm khách ...` (add)
- Cảnh báo trùng lịch khi tạo/clone/đổi giờ — cả lịch lặp (tự expand occurrences)

**Phase 3 — Nhóm D mục 8+9 (reminder + digest)** — commit `657d007`
- **Auto reminder 30p trước meeting:** polling mỗi 60s, cửa sổ 30±2 phút, dedupe qua `events.reminders_sent` (column mới). Cover cả recurring instances.
- **Daily digest 7h sáng:** 1 lần/ngày, dedupe qua `bot_meta.last_digest_date`. Liệt kê toàn bộ lịch hôm nay (1-time + recurring) sort theo giờ.
- `/today` — on-demand agenda command
- Chạy trong asyncio loop của webhook listener (KHÔNG thêm process/thread). Keep-alive GitHub Actions đảm bảo loops không bị gián đoạn vì Render sleep.

**Phase 4 — Nhóm B (bulk operations):** deprioritized theo chị ("B làm sau cùng"). Chưa build: `dời hết lịch thứ 7 sang chủ nhật`, `huỷ hết lịch tuần sau`.

**Phase 6 — External Calendar edit/delete qua bot (2026-04-23)** — commit `3a9efda`
- Trong /list, external events giờ có nút `E1`/`E2`... (trước đây chỉ display-only).
- Bấm E# → detail view với ✏️ Sửa / 🗑 Xoá, menu 6 field (giờ/ngày · thời lượng · thêm/bỏ khách · tên · nội dung), confirm có notify-email toggle (giống flow của lịch bot tạo).
- Callbacks mới: `ext_sel:<idx>`, `ext_ed_menu`, `ext_ed_f:<field>`, `ext_ed_confirm`, `ext_ed_cancel`, `ext_del_confirm`. Dùng `ctx.chat_data["list_externals"]` để resolve idx → occurrence khi callback trigger.

**Phase 7 — HY lịch cá nhân (Meet-only, private) (2026-04-23)** — commit `eaef174`
- Keyword `HY "Tên":` thay cho `Tạo lịch` → bot auto-gen Google Meet link, Calendar event với `visibility="private"` (sếp Đạt chỉ thấy busy-block, không xem được nội dung), **KHÔNG tạo Zoom**, mô tả không ghi tên John Academy.
- Vẫn mời được khách (invite qua Calendar + Meet join link), recurring hàng tuần support bình thường.
- Parse 100% format cũ — chỉ khác prefix. Ví dụ: `HY "Mentor 1-1":\n- Thời gian: 10h sáng thứ 6 hàng tuần trong 8 tuần liên tiếp bắt đầu từ 1/5/2026\n- Thời lượng: 60 phút\n- Nội dung: coaching\n- Khách: linh@abc.com`.
- DB column mới: `events.provider` (`"zoom"` default hoặc `"meet"`), `events.meet_join_url`. Idempotent ALTER TABLE migration.
- `ParsedCommand.is_personal=True` cho HY. `EventRow.provider=="meet"` → tất cả Zoom API calls bị skip (create, update, delete, occurrence ops, sync drift push, `_fetch_occurrences`).
- UI markers: `🔒` hiện trong /list summary, detail view, daily digest, 30-min reminder. Reminder HY show `🔗 [Google Meet](...)` thay vì Zoom link.
- Calendar event summary: `[HY] <topic>` (không phải `[John Academy] <topic>`).
- Flow tạo 2-bước: (1) `create_event(with_meet=True, visibility="private")` với description placeholder, (2) sau khi Google trả `hangoutLink`, PATCH description embed Meet link thật. Bước 2 non-fatal nếu fail.
- Files đụng: `bot/parser.py` (+30), `bot/db.py` (+35), `bot/calendar_client.py` (+15 — `with_meet`/`visibility`/`notify` kwargs trên create_event), `bot/formatter.py` (+80 — `format_personal_success_reply`, `format_personal_calendar_description`, 🔒 markers), `bot/handlers.py` (+130 — `_do_create_personal`, skip Zoom branches trong `_apply_edit`/`_do_delete`/`_do_delete_occurrence`/`_apply_occurrence_edit`/`_apply_drift`/`_fetch_occurrences`, /help section HY), `bot/scheduler.py` (+25 — HY-aware reminder + digest).

**Phase 5 — External Calendar integration (2026-04-23)** — commit `5b9b934`
- Bot đọc được lịch chị Yến tự tạo trên Google Calendar (không chỉ lịch bot tạo). Trước Phase 5: /list, /today, digest, reminder chỉ thấy lịch trong DB Turso.
- `CalendarClient.list_events_in_range(time_min_iso, time_max_iso)` — paginate events().list với `singleEvents=True` → 1 dict/occurrence.
- New module `bot/external_events.py` — normalize Calendar raw → `ExternalOccurrence`, dedupe via `calendar_event_id` và `recurringEventId` matching DB.
- Schema: thêm `external_reminders_sent(calendar_event_id, occurrence_iso, sent_at)` — dedup store cho 30-min reminder trên external events.
- /today + daily digest 07:00: merge DB + external, sort theo giờ. Icon: 🎯 (bot one-off) / 🔁 (bot recurring) / 📅 (external Calendar).
- 30-min reminder: polling fetch external trong window 28-32 phút, gửi reminder riêng không Zoom link, dedupe qua external_reminders_sent.
- /list có date filter (`tuần này`, `mai`, `27/4-4/5`, `tháng 5`) → append section "📅 N lịch từ Calendar (không do bot tạo)". Numbered buttons chỉ cho DB rows, external display-only.
- External events không edit qua bot được (không có Zoom link, không có DB row) → chỉnh qua Calendar UI.
- Timezone handling: Calendar API trả RFC3339 với offset → convert về naive Asia/Ho_Chi_Minh local time match DB format.
- Files đụng: `bot/calendar_client.py` (+36), `bot/external_events.py` (new 210 dòng), `bot/db.py` (+30), `bot/scheduler.py` (+65), `bot/handlers.py` (+24), `bot/formatter.py` (+20).

**/help rewritten** — commit `56fdfff` — 14 sections, thêm 📧 section "Gửi email cho khách hay không" cho edit/delete notify toggle. Bản Markdown cho `/help` command.

**Pinned help script** — commit `20a5e55` — `scripts/send_pinned_help.py` gửi pinned message dạng HTML (parse_mode=HTML để tránh Markdown italic `_` lệch cặp). Message ID 153 đã pin trong chat chị Yến.

**Schema changes (db):** thêm `events.reminders_sent` (JSON), `bot_meta` table (key/value) cho `last_digest_date`.

**Known pending issue (chờ chị confirm):** sau session [708], chị nói "test /list mai không thấy kết quả khi bot sleep" — có thể là Render cold start timeout của lần ping đầu sau idle. Keep-alive workflow (10p cron) đã deploy nên từ 2026-04-23 không nên tái xuất.

**Phase 8 — 2 hot-fix sau /help rewrite (2026-04-24)** — commits `9309391` + `6f0f9c5`
- **Bug 1: `/start` + `/help` silently fail** — commit `9309391`. `_HELP_TEXT` sau khi rewrite 10 sections = 5611 chars, vượt Telegram sendMessage limit 4096 → API trả 400 → bot không reply → chị thấy "không phản hồi". Fix: chia thành `_HELP_TEXT_PART1` (Sections 1-3: Lệnh nhanh + Tạo lịch + /list) + `_HELP_TEXT_PART2` (Sections 4-10), helper `_send_help()` reply 2 tin liên tiếp. Pinned help script cũng chia 2 (commit `6521387`).
- **Bug 2: reminder 30p + digest 7h không fire — timezone bug** — commit `6f0f9c5`. Render container chạy UTC, nhưng DB lưu naive Asia/Ho_Chi_Minh. `datetime.now()` ở Render trả UTC → lệch 7h. Reminder query window luôn lệch 7h → không bao giờ hit 28-32min → nhắc không fire. Digest `now.hour==7` fire lúc 07:00 UTC = 14:00 VN, không phải 07:00 VN. Fix: thêm `scheduler._now_vn()` dùng `ZoneInfo(config.TIMEZONE)` rồi strip tzinfo (match DB format). Dùng cho `_reminder_tick`, `_digest_tick`, `cmd_today`. Chị confirm local test `/start` + `/today` OK ngay sau deploy, reminder + digest mai kiểm tra.

**Lesson for future Telegram bots:**
1. Telegram sendMessage giới hạn 4096 chars — mọi long help/digest phải chunk. Nếu deploy infra mà user thấy "không phản hồi" sau push /help mới → check length đầu tiên.
2. Bot lưu naive datetime trong DB (Asia/Ho_Chi_Minh) nhưng Render containers chạy UTC → mọi `datetime.now()` phải convert qua `ZoneInfo` rồi strip tzinfo. Bug im lặng (log không lỗi) vì query chỉ trả empty. Check trước khi deploy: test `_now_vn()` delta vs `datetime.now()` trên Render.

**How to apply:** Khi chị nhắc tới "clone lịch", "/today", "nhắc trước 30 phút", "digest 7h", "/list tuần này", "sửa/xoá external qua /list", "lịch HY / Meet / private / cá nhân", hay "help chia 2 tin ghim" → đã implement, không cần suggest lại. Bulk operations (dời/huỷ hàng loạt nhiều lịch 1 lúc) CHƯA có — nếu chị cần thì build Phase 4.
