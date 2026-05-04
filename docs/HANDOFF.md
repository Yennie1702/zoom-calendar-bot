# JA Scheduler Bot — Handoff cho session Claude tiếp theo

*Cập nhật: 2026-05-02. Đọc file này đầu tiên khi mở session mới.*

---

## 🎯 TL;DR — Trạng thái hiện tại (2026-05-02 cuối ngày)

Bot deployed Render Free, hoạt động 24/7. Phase 3 multi-user hoàn tất. **Repo đã public + env đã set đủ. Đang monitor reminder/digest timing fix.**

**Latest commit**: pending push (HANDOFF update).

**Đã làm xong** Phương án C:
- ✅ Refactor `bot/users_config.py` load USERS từ env (commit `1437b58`)
- ✅ Gitignore `data/members.json` (Turso production + local dev)
- ✅ Render env `USERS_CONFIG_JSON` set xong — bot active với 3 users (Yến/Hương/Thuỳ)
- ✅ Audit git history: clean (không có token leaked)
- ✅ Convert repo public — `gh repo edit ... --visibility public`

**Spec reminder/digest cuối (clarify 2026-05-02 từ chị Yến)** — xem chi tiết Bug 3:
- Personal chat 1-1: ✅ reminder 30p (bot + Calendar) + ✅ HY Meet + ✅ digest 7h sáng
- Group team: ✅ reminder 30p (mọi lịch ai tạo cũng nhắc) — ❌ KHÔNG digest
- Email reminder Gmail "Lịch Google: Thông báo..." → ĐÃ FIX commit `9cad917` (Calendar popup-only). Lịch cũ vẫn còn email 1 lần cuối.

**Pending verify (1-2 ngày sau)**:
- Reminder 30p có fire đúng không?
- Digest 7h sáng có đến đúng giờ không?

```
git log --oneline -10
9cad917 Calendar event: bỏ email reminder, chỉ giữ popup notification
1437b58 Refactor users_config + members.json sang env-based (chuẩn bị repo public)
fb2c7be Zoom defaults: join_before_host 15p, no waiting room, auto cloud record
8108718 Add docs/usecase-build-bot.md — guide non-tech tự xây bot tương tự
29c9430 Fix: tất cả reply text dynamic /list ↔ /mylist theo chat_mode
```

---

## ✅ Env Phase 3 đã set đủ (2026-05-02)

5/5 env vars đã set trên Render dashboard:
- `TELEGRAM_OWNER_USER_ID` = `8173041182`
- `TELEGRAM_GROUP_CHAT_ID` = `-5136308743`
- `GOOGLE_CALENDAR_PERSONAL_ID` = `primary`
- `GOOGLE_CALENDAR_TEAM_ID` = `c_85a9e82f...@group.calendar.google.com`
- **`USERS_CONFIG_JSON`** = JSON 3 users ✅ chị Yến đã set xong

Bot đang chạy với code mới. Verify: audit log có entry `/list Hải Yến → success` lúc 09:50 UTC = 16:50 VN ngày 2/5.

---

## 📦 Pending — chị Yến phải làm tay

### 1. Set Render env `USERS_CONFIG_JSON` (BLOCKER)

Mở Render dashboard → service `ja-scheduler-bot` → tab **Environment** → **Add environment variable**:

- Key: `USERS_CONFIG_JSON`
- Value (1 dòng JSON):

```json
[{"user_id":8173041182,"display_name":"Hải Yến","email":"nguyenthihaiyen@john.vn","role":"admin","team":"John Academy","calendar_color":"6","title_prefix":"[John Academy] ","signature":"Trân trọng,\nHải Yến | PM dự án | John Academy\nZalo/SĐT: 0966863797","telegram_username":""},{"user_id":8699500614,"display_name":"Quỳnh Hương","email":"ngoquynhhuong@john.vn","role":"member","team":"JoyClub","calendar_color":"9","title_prefix":"","signature":"Người phụ trách: Quỳnh Hương - JoyClub\nZalo/SĐT: 0352118348","telegram_username":""},{"user_id":5069935322,"display_name":"Vũ Kim Thuỳ","email":"vukimthuy@john.vn","role":"member","team":"JohnBook","calendar_color":"10","title_prefix":"","signature":"Người phụ trách: Vũ Kim Thuỳ - JohnBook\nZalo/SĐT: 0389995944","telegram_username":""}]
```

→ Save → Render auto restart ~30s.

### 2. ✅ Convert repo public — DONE (2026-05-02)

Phương án C completed:
- `bot/users_config.py` load USERS từ env (commit `1437b58`)
- `data/members.json` gitignored, fallback Turso
- Audit git history: clean (không có token/secret thật)
- `gh repo edit Yennie1702/zoom-calendar-bot --visibility public --accept-visibility-change-consequences` ✓
- Repo verify: `visibility: public, private: false` ✓

**Sensitive data trong git history** (accept — work emails + Telegram user_id):
- `data/members.json` (committed Phase 11) — 10 email team
- `bot/users_config.py` versions cũ — 3 user_id Telegram + email + tên

→ Email là work emails đã dùng làm contact công khai trong company. user_id Telegram không phải password (chỉ là số định danh). Trade-off chấp nhận để có cron precision <1 phút.

**Next**: monitor reminder/digest cron timing 1-2 ngày để confirm fix triệt để.

### 3. Tin pin trong group (optional)

Tin intro `message_id=465` em gửi vào group có link Drive `.docx`. Chị Yến đã pin tay (hoặc chưa — chị verify). Nếu cần update sau, dùng API editMessageText.

---

## 📋 Bug đã biết — chưa fix

### Bug 1: Reminder 30p Telegram KHÔNG fire — EXPECTED FIXED (2026-05-02)

**Triệu chứng cũ**: chị Yến phản ánh không nhận tin Telegram 30p trước event 9h sáng. DB `external_reminders_sent` 0 entries; `events.reminders_sent` toàn `[]`.

**Root cause**: GitHub Actions cron `*/5` thực tế chỉ chạy 3-6 lần/ngày (jitter free tier private repo) → window 25-35p miss.

**Fix**: Repo convert public (Phương án C, 2026-05-02). Cron public repo precision <1 phút thay vì 1-6h. **Cần monitor 1-2 ngày để confirm fix triệt để.**

→ Verify lần tới: kiểm tra `external_reminders_sent` + `events.reminders_sent` có entries cho lịch hôm sau không. Nếu vẫn rỗng → debug tiếp.

### Bug 2: Digest 7h sáng → fire vào chiều — EXPECTED FIXED (2026-05-02)

**Triệu chứng cũ**: chị nhận digest lúc 13-15h thay vì 7h sáng.

**Root cause**: 2 nguyên nhân chồng:
1. GitHub Actions cron `0 0 * * *` private delay 6-7h
2. Render Free sleep — internal scheduler wake bất kỳ lúc nào trong window 7-18h

**Fix**: Repo public → cron `0 0 * * *` precision tốt hơn. Render bot internal scheduler vẫn có thể wake muộn nhưng workflow public sẽ catch lúc 7h, set `last_digest_date` → bot internal skip → digest đến chị Yến lúc 7h.

→ Verify sáng mai (3/5): chị nhận digest ~7h sáng VN.

### Bug 3 (clarify từ chị 2026-05-02) — IMPORTANT

Chị Yến clarify spec (chốt cuối):

**3 tính năng — chỉ áp dụng cho Personal mode (chat 1-1 chị Yến):**
- ✅ Reminder ~30 phút trước mỗi buổi (cả lịch bot tạo + lịch Calendar không do bot tạo)
- ✅ Lịch HY: reminder hiện 🔗 Google Meet thay vì Zoom
- ✅ 07:00 sáng: digest toàn bộ lịch trong ngày (sort theo giờ, icon 🎯/🔁/📅/🔒)

**Group mode — CHỈ áp dụng:**
- ✅ Reminder 30 phút trước event (nhắc TẤT CẢ lịch mọi người trong team tạo, không filter chỉ lịch của caller)
- ❌ KHÔNG có digest 7h sáng (Yến không muốn spam group)

**Bonus phát hiện chị Yến 2026-05-02**: reminder 30p **được gửi qua email Gmail** (subject "Lịch Google: Thông báo: [HY] FIT - Hẹn..." lúc 08:29) — KHÔNG phải bot Telegram.

→ Nguyên nhân: Calendar event em set `reminders.overrides = [popup 1day, email 30min]` lúc tạo event. Calendar tự gửi email 30p trước → spam Gmail chị.

→ Fix `9cad917` (đã commit + push): thay `email` override bằng `popup` → Calendar chỉ notification bell, không spam Gmail. Bot Telegram vẫn lo phần nhắc qua chat.

→ **Lịch CŨ vẫn email** vì Calendar API không bulk-update reminders retroactive. Chị Yến edit/sync từng cái mới apply. Hoặc accept lịch cũ chạy email 1 lần cuối.

Code hiện tại đã correct cho spec này:
- `trigger_reminders.py` route theo `chat_mode`: group → GROUP_CHAT_ID, personal → OWNER_USER_ID
- `trigger_digest.py` chỉ gửi vào `OWNER_USER_ID` → không spam group ✓
- Format reminder cho HY (`provider='meet'`) hiển thị Meet link thay Zoom (Phase 7) ✓

**Vẫn pending**: Telegram reminder không fire (bug 1) + digest fire chiều (bug 2) — chờ Phương án C.

---

## ✅ Đã làm 29/4 → 2/5

### 29/4 — Phase 3 hot-fixes

- `186ba5b` handle_callback bug — group click silent-drop → fix qua resolve_context
- `189e768` → `8d68112` Gộp notify_* vào reply confirm + fix calendar_id propagation cho 6 API calls (delete/edit/sync/occurrence)
- `099059d` Yến (admin) trong group bỏ enforce color → dùng default Calendar TEAM
- `adb2628` Display name Hương→Quỳnh Hương, Thuỳ→Vũ Kim Thuỳ + backfill
- `37d311c` CRITICAL privacy fix: /mylist trong group leak lịch của Yến cho member
- `1f1a7de` Hint /list → /mylist trong group reply confirm

### 30/4 — UX improvements

- `cbd896f` Update design doc Section 17-25 (Phase 3 multi-user)
- `29c9430` Fix tất cả reply text dynamic /list ↔ /mylist theo chat_mode (8 chỗ)

### 1/5 — Docs + Drive

- `8108718` Add `docs/usecase-build-bot.md` guide non-tech
- Upload `docs/team-onboarding.docx` Drive folder `1t8gwVRCHnY_vLWfPxO-99S20gl62Irqp` (id `14HNLTP3QRZAHSkDUuLR5TtnGHK01oKcW`)
- Send + chị pin tin intro msg `465` vào group với Drive link

### 2/5 — Zoom + email + refactor

- `fb2c7be` Zoom defaults: join_before_host 15p, no waiting room, auto cloud record
- `1437b58` Refactor users_config + members.json sang env-based (chuẩn bị public)
- `9cad917` Calendar bỏ email reminder, chỉ popup

---

## 🗂 File location reference

| Việc | File |
|---|---|
| Architecture/feature design | `design/02-feature-design.md` (Section 17-25 = Phase 3) |
| System architecture | `design/01-system-design.md` |
| Onboarding cho member | `docs/team-onboarding.md` + `docs/team-onboarding.docx` |
| Hướng dẫn build bot tương tự | `docs/usecase-build-bot.md` |
| Permission gate logic | `bot/permissions.py` |
| USERS config (load env) | `bot/users_config.py` |
| Multi-user create/edit flow | `bot/handlers.py` |
| Audit log helpers | `bot/db.py` (cuối file) |
| Migration scripts | `scripts/migrate_phase3.py`, `scripts/migrate_display_names.py` |
| Reseed Turso members | `scripts/reseed_members.py` |
| Upload secrets | `scripts/upload_secrets.sh` (chị paste vào Terminal local) |
| GitHub Actions workflows | `.github/workflows/{daily-digest,reminders,keep-alive}.yml` |
| Backup pipeline | `scripts/backup_*.{py,sh}` (launchd local 23:00 daily) |

### Sensitive data NOT in repo

| File | Storage |
|---|---|
| `.env` (token Zoom/Google/Telegram/Turso) | Local only (gitignored) |
| `data/members.json` | Local (gitignored) + Turso `members` table |
| Render env `USERS_CONFIG_JSON` | Render dashboard |

---

## 🚦 Memory + Backup

**Memory** (Claude session memory, separate khỏi repo):
- `~/.claude/projects/-Volumes-Space-Claude-Projects-zoom-calendar-bot/memory/`
- Index: `MEMORY.md` (bullets + links)
- Phase 3 lessons: `reference_github_actions_keep_alive.md` (đã update gotchas)

**Backup**:
- launchd local 23:00 daily
- DB dump → `data/backups/db_*.sql.gz` (90 ngày)
- Memory snapshot → `data/memory_backups/`
- Drive upload → folder `JA-Scheduler-Backups` (cùng folder chứa `team-onboarding.docx`)

---

## 🧪 Test ngay đầu session sau (5 phút)

Sau khi mở session mới + chị Yến confirm `USERS_CONFIG_JSON` đã set:

```bash
cd /Volumes/Space/Claude/Projects/zoom-calendar-bot
curl -s -X POST -o /dev/null -w "Webhook: %{http_code}\n" -H "Content-Type: application/json" -d '{"update_id":0}' https://ja-scheduler-bot.onrender.com/telegram
# Expected: 200
```

Trong Telegram group:
- Hương gõ `/whoami` → bot reply "Tên Telegram: Quỳnh Hương..." → ✓
- Hương gõ `/mylist` → bot reply lịch của Quỳnh Hương (KHÔNG hiện lịch Yến)

Sau đó hỏi chị Yến: "Chị OK convert repo public chưa? Em làm tiếp."

---

## 📞 Contact info

- Owner: chị Hải Yến (`nguyenthihaiyen@john.vn`, Telegram user_id 8173041182)
- Group team Telegram: `-5136308743` (JA Scheduler Team)
- Render service: `ja-scheduler-bot.onrender.com`
- GitHub repo: https://github.com/Yennie1702/zoom-calendar-bot
- Turso DB: `TURSO_DATABASE_URL` trong .env
- Calendar TEAM id: `c_85a9e82f617195b8fb44a1c10f0fb5e191a49905a0c83ef4d548f8dd1758b0f5@group.calendar.google.com`

---

*File này nên đọc đầu tiên mỗi session. Cập nhật mỗi khi có change đáng kể (commit lớn, bug mới, decision pending).*
