# JA Scheduler Bot

Telegram bot tự động tạo Zoom meeting + Google Calendar invite cho chị Hải Yến
(John Academy). Single-user, long-polling, deploy trên Render Free tier.

---

## Tính năng

- Tạo lịch one-time hoặc recurring (hàng tuần) từ 1 tin nhắn tiếng Việt
- Tự động: Zoom meeting + Google Calendar event + gửi invite email cho khách
- Quản lý: `/list` xem 10 lịch gần nhất, sửa/xoá theo button
- Quick-edit tự nhiên: `sửa giờ 15h`, `thêm khách a@x.vn`, `xoá lịch`…
- Recurring: xoá hoặc sửa 1 buổi riêng (không ảnh hưởng series)
- Kéo thả trên Google Calendar → `/sync` đồng bộ ngược về Zoom + DB

Chi tiết lệnh → nhắn `/help` cho bot.

---

## Chạy local

```bash
# 1. Clone + venv
git clone <repo-url>
cd zoom-calendar-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Tạo .env (copy từ .env.example rồi điền credentials)
cp .env.example .env
# Mở .env bằng editor, điền: TELEGRAM_BOT_TOKEN, ZOOM_*, GOOGLE_*

# 3. Lần đầu: lấy Google refresh token
python get_refresh_token.py
# → copy dòng GOOGLE_REFRESH_TOKEN=... paste vào .env

# 4. Chạy bot
python -m bot.main
```

Bot sẽ long-poll Telegram, không cần public URL. Ctrl+C để dừng.

---

## Deploy lên Render (Free tier)

**Tiền đề:** Render free Web Service không có background worker miễn phí, nên bot
chạy kèm HTTP health server trên `$PORT` (xem [bot/main.py](bot/main.py)).
Sau 15 phút không có HTTP request, Render sleep service → tin nhắn Telegram
đầu tiên sẽ chậm 30–60s để wake up. Sau đó chạy bình thường.

### Các bước

1. **Push code lên GitHub private repo** (xem `.gitignore` đã loại trừ `.env`, `data/`, `logs/`).
2. **Tạo Render service:**
   - Sign up → New + → Blueprint → chọn repo.
   - Render đọc `render.yaml` → tạo Web Service tự động.
   - Hoặc manual: New + → Web Service → connect repo, build command `pip install -r requirements.txt`, start command `python -m bot.main`.
3. **Điền Environment Variables trên Render dashboard** (bắt buộc — `render.yaml` chỉ khai báo key, giá trị không commit):

   | Biến | Lấy ở đâu |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | @BotFather trên Telegram |
   | `TELEGRAM_ALLOWED_CHAT_ID` | Chat ID cá nhân (chạy `python get_chat_id.py` hoặc xem từ @userinfobot) |
   | `ZOOM_ACCOUNT_ID` | Zoom Marketplace → S2S OAuth app |
   | `ZOOM_CLIENT_ID` | ↑ |
   | `ZOOM_CLIENT_SECRET` | ↑ |
   | `GOOGLE_CLIENT_ID` | Google Cloud Console → OAuth 2.0 Client ID |
   | `GOOGLE_CLIENT_SECRET` | ↑ |
   | `GOOGLE_REFRESH_TOKEN` | `python get_refresh_token.py` ở local |
   | `GOOGLE_CALENDAR_ACCOUNT` | Email calendar dùng (vd `nguyenthihaiyen@john.vn`) |
   | `CONTACT_NAME` | Tên hiển thị trong invite (vd `Hai Yen`) |
   | `CONTACT_TITLE` | Chức danh (vd `PM du an - John Academy`) |

4. **Deploy** — Render tự build + start. Theo dõi "Logs" tab đến khi thấy:
   ```
   Bot is polling…
   Health server listening on 0.0.0.0:10000
   ```
5. **Test:** nhắn `/start` cho bot qua Telegram.

### Zoom S2S OAuth scopes cần

- `meeting:write:admin`, `meeting:read:admin`
- `meeting:update:meeting:admin`, `meeting:delete:meeting:admin`

Sau khi chỉnh scope trong Zoom Marketplace, nhớ **Deactivate → Activate** app để áp dụng.

### Google OAuth

- Loại: OAuth 2.0 Client ID (Desktop application)
- Scope: `https://www.googleapis.com/auth/calendar.events`
- Chạy `get_refresh_token.py` 1 lần ở local, refresh token dùng mãi (không hết hạn trừ khi bị revoke).

---

## Quản lý env vars trên Render

- **Đổi secret:** Dashboard → service → Environment → Edit → Save → Render tự redeploy.
- **Xem hiện tại:** Dashboard → Environment tab. Render mask giá trị mặc định.
- **Rotate token:** đổi ở provider (Telegram/Zoom/Google) → update trên Render → redeploy.

---

## Debug

### Xem logs
- **Render dashboard → Logs tab** — real-time stream. Có filter + search.
- Log level mặc định `INFO`. Đổi bằng env var `LOG_LEVEL=DEBUG` nếu cần.

### Bot không trả lời
1. Check Render "Events" tab — service đang Running hay Suspended?
2. Free tier sleep sau 15 phút idle — tin nhắn đầu phải đợi 30–60s wake.
3. Logs có line `"Bot is polling…"` không? Nếu không → build fail hoặc crash.
4. Logs có `"Rejected message from unauthorized chat_id=..."` → `TELEGRAM_ALLOWED_CHAT_ID` sai.

### Lịch tạo fail
- Logs có traceback `"Zoom create_meeting failed 400"` → thường do scope thiếu hoặc token hết hạn.
- `"Invalid access token, does not contain scopes:[...]"` → vào Zoom Marketplace thêm scope → Deactivate + Activate app.
- `"Google credentials incomplete"` → env var `GOOGLE_*` chưa đủ 3 cái (CLIENT_ID, CLIENT_SECRET, REFRESH_TOKEN).

### DB reset sau deploy
Free tier Render dùng ephemeral disk → mỗi lần redeploy, `data/events.db` bị xoá.
Lịch đã tạo trên Zoom + Calendar **vẫn còn** (ở server Zoom/Google), chỉ mất history
local. Vẫn có thể tạo lịch mới. Nếu cần persist DB: nâng Render Persistent Disk
($1/tháng) hoặc chuyển sang external DB (Supabase/Neon free tier).

---

## Kiến trúc file

```
.
├── bot/
│   ├── main.py           ← entrypoint, health server + PTB Application
│   ├── config.py         ← env loader (.env hoặc Render env vars)
│   ├── handlers.py       ← Telegram command + callback handlers
│   ├── parser.py         ← parser tiếng Việt cho "Tạo lịch …"
│   ├── formatter.py      ← format preview/reply cho Telegram + Calendar
│   ├── db.py             ← SQLite (events.db)
│   ├── zoom_client.py    ← Zoom S2S OAuth + meeting CRUD
│   └── calendar_client.py ← Google Calendar v3 API
├── scripts/              ← one-off scripts (send_pinned_help, cleanup…)
├── get_refresh_token.py  ← chạy 1 lần để lấy Google refresh token
├── requirements.txt
├── render.yaml           ← Render Blueprint
├── .env.example
└── .gitignore
```

---

## Bảo mật

- Toàn bộ credentials qua env vars, không hardcode.
- `.gitignore` loại trừ `.env`, `data/*.db`, `logs/`.
- Chỉ chat ID đã whitelist (`TELEGRAM_ALLOWED_CHAT_ID`) gửi lệnh được. Tin nhắn từ chat khác bị reject + log warning.
- Mỗi lệnh sửa/xoá có preview + confirm (tránh gửi email khách nhầm).
- Zoom S2S token in-memory, auto-refresh 60s trước khi hết hạn.
