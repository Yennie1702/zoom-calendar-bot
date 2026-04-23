"""One-off: send the pinnable help message to chị Yến's chat.

Run: python scripts/send_pinned_help.py
After it prints OK, pin that message manually in Telegram.
"""
from __future__ import annotations

import html
import sys
import requests

sys.path.insert(0, ".")
from bot import config  # noqa: E402


def _cb(code: str) -> str:
    """Wrap a multi-line code block, escaping HTML special chars."""
    return f"<pre>{html.escape(code)}</pre>"


def _c(s: str) -> str:
    """Inline code."""
    return f"<code>{html.escape(s)}</code>"


HELP = f"""📖 <b>JA Scheduler Bot — Hướng dẫn đầy đủ</b>
<i>Bot ghép Zoom + Google Calendar, chạy 24/7 trên Render.</i>

📌 <b>1. LỆNH NHANH</b>
• {_c('/start')}, {_c('/help')} — hướng dẫn
• {_c('/list')} — quản lý lịch (xem · sửa · xoá · tìm · lọc)
• {_c('/today')} — lịch hôm nay (digest on-demand)
• {_c('/sync [id]')} — đồng bộ sau khi kéo thả trên Calendar

🆕 <b>2. TẠO LỊCH</b>

<b>2A. Lịch công việc (Zoom + Calendar, mời khách)</b>
<i>One-time:</i>
{_cb('''Tạo lịch "Tư vấn OKRs - Chị Lan":
- Thời gian: 22/4/2026 14:00
- Thời lượng: 30 phút
- Nội dung: Tư vấn gói Coaching OKRs
- Khách: lan@abc.com''')}
<i>Recurring (hàng tuần):</i>
{_cb('''Tạo lịch "Mentor MBOs 42":
- Thời gian: 8h30 sáng thứ 4 hàng tuần trong 12 tuần liên tiếp bắt đầu từ 20/5/2026
- Thời lượng: 120 phút
- Nội dung: Chương trình Mentor MBOs
- Mời khách: a@x.vn, b@y.vn''')}
→ Bot parse → preview → bấm <b>✅ Xác nhận tạo</b>.

<b>2B. Lịch HY — cá nhân</b> 🔒 <i>(chỉ mình chị, Meet thay Zoom, private)</i>
Keyword {_c('HY')} thay {_c('Tạo lịch')}. Bot:
• Auto-sinh link <b>Google Meet</b>, KHÔNG tạo Zoom
• Set <b>visibility: private</b> → sếp/đồng nghiệp chỉ thấy busy-block, không xem được nội dung
• Không ghi tên John Academy trong mô tả
• Vẫn mời được khách tuỳ chọn, recurring hàng tuần OK
<i>One-time (chỉ mình chị):</i>
{_cb('''HY "Check-in sức khoẻ":
- Thời gian: 25/4/2026 9:00
- Thời lượng: 30 phút
- Nội dung: Tự review tuần''')}
<i>Recurring + mời khách riêng:</i>
{_cb('''HY "Mentor 1-1 Linh":
- Thời gian: 10h sáng thứ 6 hàng tuần trong 8 tuần liên tiếp bắt đầu từ 1/5/2026
- Thời lượng: 60 phút
- Nội dung: Coaching cá nhân
- Khách: linh@abc.com''')}
→ Trong /list hiện dấu 🔒 cạnh tên lịch HY.

<b>2C. Clone lịch cũ</b> (copy rồi chỉnh, nhanh hơn gõ lại)
{_cb('''tạo lịch giống #5
tạo lịch giống #5 nhưng ngày 27/4 15h
tạo lịch giống "Tư vấn OKRs" nhưng ngày mai, khách a@x.vn
tạo lịch giống #3 nhưng tên "OKRs v2", thêm khách b@y.vn''')}

📋 <b>3. QUẢN LÝ LỊCH — /list</b>

<b>3A. Mặc định</b> — {_c('/list')} hiện 10 lịch gần nhất:
• Mỗi lịch 1 nút số (1-10) → bấm → detail
• Detail có nút <b>✏️ Sửa</b> / <b>🗑 Xoá</b>
• Menu ✏️: 6 field — giờ/ngày · thời lượng · thêm khách · bỏ khách · tên · nội dung
• 🔒 cạnh tên = lịch HY cá nhân

<b>3B. Tìm &amp; lọc &amp; phân trang</b>
{_cb('''/list 2                   ← trang 2 (10 lịch/trang)
/list OKRs                ← lọc từ khoá trong tên/nội dung
/list khách lan@abc.com   ← lọc theo email khách
/list tuần này
/list tuần sau | tuần trước
/list hôm nay | mai | hôm qua
/list tháng này | tháng 5 | tháng 5/2026
/list 27/4                ← ngày cụ thể
/list 27/4-4/5            ← khoảng ngày
/list OKRs 2              ← từ khoá + trang''')}

<b>3C. Lịch Calendar không do bot tạo</b> 📅
Khi /list có lọc theo ngày, bot thêm section "📅 N lịch từ Calendar" với nút <code>E1</code>/<code>E2</code>…
• Bấm <code>E#</code> → detail + ✏️ Sửa / 🗑 Xoá luôn qua bot (không cần mở Calendar)
• Menu ✏️ 6 field giống lịch bot tạo
• Notify-email toggle hoạt động bình thường

⚡ <b>4. SỬA NHANH TỪ CHAT</b> (không cần /list)

<b>4A. Sửa lịch mới nhất</b> — nhắn thẳng:
{_cb('''sửa giờ 15h30
sửa giờ 15h30 25/4/2026
sửa thời lượng 45 phút
sửa tên Tư vấn OKRs v2
sửa nội dung Nội dung mới
thêm khách a@x.vn, b@y.vn
bỏ khách a@x.vn
xoá lịch''')}

<b>4B. Sửa lịch khác — bằng #id</b> (lấy id từ /list):
{_cb('''sửa giờ 15h #5
thêm khách a@x.vn #5
xoá lịch #5''')}

<b>4C. Sửa lịch cũ bằng TÊN</b> (không cần nhớ id):
{_cb('''sửa giờ 15h30 "Tư vấn OKRs" ngày 25/4
xoá lịch "Tư vấn OKRs" ngày 25/4
xoá lịch khách lan@abc.com
sửa thời lượng 45 phút "Mentor MBOs"''')}
→ Nhiều lịch khớp → bot hiện list để chị bấm số chọn.

🔁 <b>5. LỊCH LẶP — SỬA/XOÁ 1 BUỔI RIÊNG</b>
• <b>Sửa 1 buổi:</b> /list → lịch lặp → ✏️ → "📝 Sửa 1 buổi riêng" → chọn buổi → Giờ / Thời lượng
• <b>Xoá 1 buổi:</b> /list → lịch lặp → 🗑 → "⦿ Chỉ 1 buổi" → chọn buổi
• <b>Xoá cả series:</b> /list → lịch lặp → 🗑 → "🗑 Toàn bộ series"

📧 <b>6. GỬI EMAIL CHO KHÁCH HAY KHÔNG</b>
Mỗi lần confirm sửa / xoá, bot hiện 2 nút:
• <b>✅ … + gửi mail</b> — Google Calendar gửi email update/huỷ cho khách
• <b>✅ … (không mail)</b> — update/huỷ âm thầm, khách không nhận email
Áp dụng cho: sửa/xoá toàn bộ lịch · sửa/xoá 1 buổi riêng · xoá series · sửa lịch Calendar không do bot tạo.

⏰ <b>7. NHẮC LỊCH &amp; DIGEST TỰ ĐỘNG</b>
• <b>~30 phút trước mỗi buổi</b> bot gửi nhắc vào chat này (cả lịch bot tạo + lịch Calendar).
• Lịch HY: reminder hiện 🔗 Google Meet thay vì Zoom.
• <b>07:00 sáng</b>: digest toàn bộ lịch trong ngày (sort theo giờ, icon 🎯/🔁/📅/🔒).
• {_c('/today')} — xem digest hôm nay bất kỳ lúc nào.

⚠️ <b>8. CẢNH BÁO TRÙNG LỊCH</b>
Khi tạo / clone / đổi giờ, nếu overlap với lịch khác (kể cả lịch lặp), bot cảnh báo ngay trong preview. Chị vẫn confirm được nếu cố ý trùng.

🔄 <b>9. ĐỒNG BỘ SAU KHI KÉO THẢ TRÊN GOOGLE CALENDAR</b>
Chị kéo thoải mái trên Calendar UI. Sau đó báo bot đồng bộ:
• {_c('/sync')} → đồng bộ lịch mới nhất
• {_c('/sync 5')} → đồng bộ lịch id=5
• Hoặc /list → bấm vào lịch: nếu có drift sẽ có banner ⚠️ + nút <b>🔄 Sync</b>
→ Bot coi Calendar là <b>nguồn đúng</b>, update Zoom + DB theo.
<i>Lịch HY (Meet) chỉ sync với Calendar, không đụng Zoom.</i>

🎨 <b>10. ICON TRONG /list &amp; DIGEST</b>
• 🎯 lịch bot tạo · 🔁 lịch bot tạo + recurring
• 🔒 lịch HY cá nhân (Meet, private)
• 📅 lịch từ Calendar (không do bot tạo)

⚠️ <b>LƯU Ý</b>
• Chỉ chị Hải Yến (chat_id whitelist) nhắn được.
• Bot luôn preview trước khi ghi thật. Gõ {_c('huỷ')} để bỏ mọi trạng thái chờ.
• /sync chỉ đồng bộ cấp series — kéo 1 instance riêng trên Calendar → dùng luồng "Sửa 1 buổi riêng" qua /list."""


def main() -> None:
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={
            "chat_id": config.TELEGRAM_ALLOWED_CHAT_ID,
            "text": HELP,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )
    print(f"status={r.status_code}")
    print(r.text[:500])
    r.raise_for_status()
    print("✅ Sent. Chị vào Telegram, giữ tin nhắn đó → Pin.")


if __name__ == "__main__":
    main()
