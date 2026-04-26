---
name: Zoom + Calendar Telegram bot (JA Scheduler)
description: Python bot deployed on Render — Telegram bot tự động tạo Zoom + Google Calendar invite cho chị Yến. Current state, deployment, env vars.
type: project
originSessionId: 7e420da6-5336-46b8-8aa1-d23684ddeb77
---
**Location:** `/Volumes/Space/Claude/zoom-calendar-bot/` (sibling với JOHNSPACE, KHÔNG phải submodule)

**Phase 2 DEPLOYED** (2026-04-22): Python bot đang chạy production trên Render Free tier.
- Host: `https://ja-scheduler-bot.onrender.com`
- Webhook: `/telegram` (registered via getWebhookInfo)
- Repo: git branch `main`, .env gitignored (chmod 600), .env.example tracked

**Stack:**
- python-telegram-bot (webhook mode) + google-auth-oauthlib + google-api-python-client
- Zoom S2S OAuth (Sếp Đạt account — `maixuandat@okrs.vn`)
- Google Calendar API v3 (OAuth user flow as `nguyenthihaiyen@john.vn` — invites "from" work email)
- Turso libSQL (database: `ja-scheduler-yennie1702.aws-ap-northeast-1.turso.io`)

**Env vars (required trên Render + .env local):**
- `TELEGRAM_BOT_TOKEN` (bot `@JA_Scheduler_bot`, id 8673346789)
- `TELEGRAM_ALLOWED_CHAT_ID` (chị Yến = 8173041182 — LÀ chat_id của user, KHÔNG phải bot id)
- `ZOOM_ACCOUNT_ID` / `ZOOM_CLIENT_ID` / `ZOOM_CLIENT_SECRET` (S2S OAuth)
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` / `GOOGLE_REFRESH_TOKEN` / `GOOGLE_CALENDAR_ACCOUNT`
- `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN`
- `CONTACT_PHONE` / `CONTACT_NAME` / `CONTACT_TITLE` / `TEST_EMAIL`

**Zoom scopes cần (phát hiện khi test):** `meeting:write:admin`, `meeting:read:admin`, `user:read:admin`, **`meeting:delete:meeting:admin`** (nếu thiếu thì không xoá/huỷ lịch được).

**OAuth app Google đã PUBLISHED** (production, không còn giới hạn 7-day refresh token). Unverified warning vẫn xuất hiện khi OAuth consent (bình thường vì không submit Google verification — click Nâng cao → link ẩn).

**Notify toggle (2026-04-22):** Edit/Delete event đều có inline keyboard chọn `✅ + gửi mail` vs `✅ không mail`. Implemented qua `notify: bool` param xuống `sendUpdates="all"|"none"`.

**Keep-alive (2026-04-23):** GitHub Actions workflow `.github/workflows/keep-alive.yml` ping service mỗi 10 phút → tránh Render free tier cold start (30–60s wake up gây Telegram timeout). Chi phí 0đ.

**Secrets rotated (2026-04-22):** Toàn bộ Google (multi-secret), Zoom, Telegram, Turso. Old secrets đã invalidated/deleted. Playbook: xem `feedback_secret_rotation.md`.

**Test command format (regex parser):**
```
Tạo lịch "Tư vấn OKRs - Chị Lan":
- Thời gian: 22/4/2026 14:00
- Thời lượng: 30 phút
- Nội dung: Tư vấn gói Coaching OKRs
- Khách: lan@abc.com
```
(Recurring: thêm "hàng tuần" + "X tuần liên tiếp".)

**How to apply:** Khi edit bot, commit + push → Render auto-deploy. Nếu thay env → Save trên Render dashboard tự trigger redeploy (~90s). Test luồng bằng `/start` (sanity) → brief format chuẩn (tạo lịch) → `/list` (DB query).
