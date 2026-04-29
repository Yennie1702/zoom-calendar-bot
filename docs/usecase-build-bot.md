# Usecase — Tự xây bot tạo lịch họp Zoom + Google Calendar qua Telegram

*Tài liệu này mô tả các bước lớn để xây 1 bot tương tự, dành cho người không-code đọc hiểu được. Tổng thời gian: 2-3 tuần làm part-time.*

---

## 1. Vấn đề muốn giải

Mỗi lần đặt lịch họp với khách:
- Mở Zoom → tạo meeting → copy link
- Mở Google Calendar → tạo event → paste link → invite từng email khách
- Lặp lại 5-10 lần/ngày

→ **Tốn 3-5 phút/lịch, dễ sai chính tả email khách.**

**Mục tiêu:** gõ 5 dòng vào Telegram → bot làm hết. Cả team (3 người) dùng được.

---

## 2. Stack — chọn công cụ nào

| Vai trò | Tool dùng | Vì sao |
|---|---|---|
| Giao diện chat | **Telegram bot** | Free, app sẵn trên điện thoại, API đơn giản |
| Tạo Zoom meeting | **Zoom API (Server-to-Server OAuth)** | Free tier 100 meeting/tháng, không cần user click |
| Tạo Calendar event + invite khách | **Google Calendar API** | Tự động gửi invite email cho khách |
| Lưu lịch sử | **Turso (SQLite cloud)** | Free tier 9GB, tương thích SQLite local |
| Host bot | **Render Web Service Free** | Free, deploy từ GitHub commit, 1-click setup |
| Code | **Python** + thư viện `python-telegram-bot` | Quen thuộc, có template Telegram bot sẵn |
| Backup | **GitHub Actions + Google Drive** | Free, không cần tự host server backup |

**Tổng chi phí:** 0₫/tháng (toàn free tier).

---

## 3. Các bước lớn xây dựng (8 bước)

### Bước 1 — Tạo Telegram bot (5 phút)

1. Mở Telegram → search **@BotFather** → gõ `/newbot`
2. Đặt tên + username → BotFather trả token
3. Lưu token (giống password) — bot dùng để gọi API

### Bước 2 — Setup Zoom + Google credentials (30 phút)

1. **Zoom Marketplace** → tạo "Server-to-Server OAuth App" → lấy Account ID + Client ID + Client Secret
2. **Google Cloud Console** → tạo project → enable Calendar API → tạo OAuth credentials → consent screen → publish app → chạy script `get_refresh_token.py` 1 lần để lấy refresh token (token sống vĩnh viễn)

→ Lưu 6 giá trị credentials vào file `.env` của project.

### Bước 3 — Code bot core (1 tuần)

Phân chia thành 5 module:
- **Parser** — đọc tin nhắn `Tạo lịch "Tên": - Thời gian: ... - Khách: ...` → cấu trúc Python
- **Zoom client** — gọi Zoom API tạo meeting
- **Calendar client** — gọi Google API tạo event + invite khách
- **DB** — lưu lịch đã tạo (để sau này sửa/xoá)
- **Handlers** — nhận tin Telegram → parse → preview → confirm → tạo

Pattern: **luôn preview trước khi ghi thật**, user bấm "✅ Xác nhận" hoặc "❌ Huỷ".

### Bước 4 — Deploy lên Render (15 phút)

1. Push code lên GitHub (private repo OK)
2. Render → New Web Service → connect GitHub repo
3. Set environment variables (token Zoom/Google/Telegram/Turso)
4. Deploy → Render tự build + chạy 24/7

### Bước 5 — Connect bot ↔ Telegram (Webhook)

1. Render trả URL kiểu `https://my-bot.onrender.com`
2. Gọi Telegram API `setWebhook` 1 lần → Telegram biết gửi tin tới đâu
3. Test gõ `/start` trong Telegram → bot phải reply

### Bước 6 — Thêm tính năng theo nhu cầu

Mỗi tính năng = 1 commit. Vài tính năng quan trọng:

| Tính năng | Mô tả |
|---|---|
| Sửa/xoá lịch | `/list` → bấm số → menu Sửa/Xoá |
| Lịch lặp | "thứ 4 hàng tuần trong 12 tuần" → bot tạo 12 buổi cùng 1 link Zoom |
| Nhắc 30 phút trước | Bot tự gửi tin "Còn 30 phút..." vào chat |
| Sổ thành viên | Lưu sẵn email team → gõ tên thay email khi mời khách |
| Multi-user | Mở rộng cho cả team dùng (xem Bước 8) |

### Bước 7 — Reliability (3-4 ngày)

Free tier có 2 vấn đề:
- **Render Free ngủ sau 15 phút idle** → bot dậy mất 30s, có khi bỏ lỡ giờ nhắc
- **GitHub Actions cron jitter** ±5-15 phút → reminder/digest đôi khi muộn

**Giải pháp:**
- GitHub Actions ping bot mỗi 5 phút (giữ ấm)
- Cho **GitHub Actions tự gửi reminder** thay vì bot Render → 100% reliable, không phụ thuộc Render uptime
- Backup hàng ngày DB + code lên Drive (tránh mất nếu Turso/Render fail)

### Bước 8 — Mở rộng cho team (1 tuần)

Bot từ 1-user thành multi-user:
1. **Xác định 2 chế độ**: chat 1-1 với chủ bot (toàn quyền) vs chat group team (theo phân quyền)
2. **File config user** (`users_config.py`): list user_id Telegram + email + role (admin/member)
3. **Permission gate**: mỗi command check `user_id in USERS` + `role` trước khi xử lý
4. **Lịch chỉ thấy lịch của mình** (`/mylist`); admin xem được tất cả (`/list`)
5. **Audit log**: mọi thao tác lưu vào DB để admin theo dõi
6. **Calendar riêng**: lịch group ghi vào Calendar TEAM (chia sẻ cho cả team), lịch personal vẫn ở Calendar primary của chủ bot

---

## 4. Lessons learned (kinh nghiệm)

### Quy tắc vàng

1. **Luôn preview trước khi ghi thật** — user phải confirm. Tránh bot tự tạo nhầm rồi không biết.
2. **Idempotent migration** — script DB chạy lại không lỗi (`UPDATE ... WHERE column IS NULL`).
3. **Permission check ở mọi entry point** — command, callback button, text. Quên 1 chỗ → leak data.
4. **Test với 2 user** trước khi cho cả team dùng — phát hiện bug như "lịch của user A hiện cho user B".

### Cạm bẫy thường gặp

- **Telegram bot mặc định privacy mode** ON trong group → bot không thấy text thường, chỉ thấy command. Phải tắt qua @BotFather + kick/add bot lại.
- **Google Calendar `delete_event`** với `calendarId=primary` mặc định → nếu event ở Calendar khác, API treat thành "decline" thay vì xoá thật. Phải truyền `calendar_id` đúng.
- **GitHub PAT** thiếu scope `workflow` → push file `.github/workflows/*.yml` bị reject. Switch git remote sang SSH bypass.
- **`gh secret set --body -` (stdin)** trên 1 số version chỉ ghi 1 ký tự đầu. Dùng flag `--body "$value"` thay vì pipe.

### Workflow phát triển

| Step | Tool |
|---|---|
| Chat với AI để spec tính năng | Claude Code (terminal) |
| AI viết code → review diff | Claude Code show diff trước commit |
| Test local (SQLite + bot polling) | `venv/bin/python -m bot.main` |
| Push GitHub → Render auto deploy | `git push origin main` |
| Verify deploy | curl webhook + check Telegram |
| Backup hàng ngày | launchd local + Drive upload |

---

## 5. Các câu hỏi non-tech hay hỏi

**Q: Code mình viết hay AI viết?**  
A: Thiết kế + spec do mình quyết. AI viết code theo spec, mình review diff trước khi push. AI làm phần lặp đi lặp lại (boilerplate, parse format). Mình quyết business logic.

**Q: Trả tiền gì không?**  
A: Hoàn toàn free tier. Render Free + Turso Free + GitHub Actions Free + Telegram Free + Zoom Free 100 meeting/tháng. Nếu vượt limit (vd > 100 Zoom meeting/tháng) thì upgrade Zoom Pro $14/tháng.

**Q: Bao lâu thì làm xong?**  
A: 2-3 tuần làm part-time (~2h/ngày). Bước 1-5 (bot 1-user) khoảng 1 tuần. Bước 6-7 (tính năng + reliability) 1 tuần. Bước 8 (multi-user) 1 tuần.

**Q: Bảo mật như thế nào?**  
A:
- Token credentials trong `.env` (gitignored, chỉ trên máy mình + Render env vars)
- Bot whitelist user_id Telegram → người lạ không gọi được
- Audit log mọi thao tác → ai làm gì lúc nào đều ghi lại

**Q: Mở rộng được tới bao nhiêu user?**  
A: Free tier khoảng 5-10 user là OK. Nếu hơn → upgrade Render Starter $7/tháng + tăng Zoom plan + có thể chuyển DB sang Postgres.

**Q: Nếu mình muốn thêm tính năng X mới, có khó không?**  
A: Pattern đã có sẵn (parser → handler → DB → Telegram reply). Mỗi tính năng mới ~1 commit ~50-100 dòng code. AI có thể viết theo pattern. Quan trọng là spec rõ ràng trước khi code.

---

## 6. Tổng kết

**Bot này là gì:** trợ lý đặt lịch họp tự động cho team 3 người, replace flow thủ công 5 phút/lịch bằng gõ 5 dòng vào Telegram.

**Có gì hay:**
- Free 100% (không tốn tiền hosting/database)
- Multi-user với phân quyền admin/member
- Tự nhắc 30 phút trước mỗi lịch
- Backup hàng ngày Drive (nếu mất bot vẫn restore được)
- Audit log mọi thao tác (admin xem ai làm gì)

**Cần gì để làm tương tự:**
- 1 GitHub account (free)
- 1 Render account (free)
- 1 Google Cloud project (free)
- 1 Zoom Pro account (hoặc Free 100 meeting/tháng)
- 1 Telegram account
- 2-3 tuần làm part-time với AI assistant (Claude Code hoặc tương tự)

**Project gốc:** [github.com/Yennie1702/zoom-calendar-bot](https://github.com/Yennie1702/zoom-calendar-bot)

---

*Tài liệu này viết bởi chị Hải Yến (PM dự án, John Academy) — 2026-04-29.*
