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


HELP = f"""📖 <b>JA Scheduler Bot — Hướng dẫn dùng</b>

🆕 <b>TẠO LỊCH MỚI</b>

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
Bot parse → preview → bấm ✅ để tạo thật.

⚡ <b>SỬA NHANH — LỊCH MỚI NHẤT</b>
Nhắn thẳng (không cần format):
{_cb('''sửa giờ 15h30
sửa giờ 15h30 25/4/2026
sửa thời lượng 45 phút
sửa tên Tư vấn OKRs v2
sửa nội dung Nội dung mới
thêm khách a@x.vn, b@y.vn
bỏ khách a@x.vn
xoá lịch''')}
Target lịch khác: thêm {_c('#id')} (lấy từ /list), VD: {_c('sửa giờ 15h #5')}

🔍 <b>SỬA/XOÁ LỊCH CŨ BẰNG TÊN</b> (không cần nhớ id)
{_cb('''sửa giờ 15h30 "Tư vấn OKRs" ngày 25/4
xoá lịch "Tư vấn OKRs" ngày 25/4
xoá lịch khách lan@abc.com
sửa thời lượng 45 phút "Mentor MBOs"''')}
Nhiều lịch khớp → bot hiện list để chị bấm số chọn.

📑 <b>CLONE LỊCH CŨ</b> (copy nhanh rồi chỉnh)
{_cb('''tạo lịch giống #5
tạo lịch giống #5 nhưng ngày 27/4 15h
tạo lịch giống "Tư vấn OKRs" nhưng ngày mai, khách a@x.vn
tạo lịch giống #3 nhưng tên "OKRs v2", thêm khách b@y.vn''')}

⚠️ <b>CẢNH BÁO TRÙNG LỊCH</b>
Khi tạo / clone / đổi giờ, nếu overlap với lịch khác, bot cảnh báo ngay trong preview. Chị vẫn confirm được nếu cố ý trùng.

📧 <b>GỬI EMAIL CHO KHÁCH HAY KHÔNG</b>
Mỗi lần confirm sửa / xoá, bot hiện 2 lựa chọn:
• <b>✅ Sửa + gửi mail</b> / <b>✅ Xoá + gửi mail</b> — Google Calendar gửi email update/huỷ cho tất cả khách.
• <b>✅ Sửa (không mail)</b> / <b>✅ Xoá (không mail)</b> — update/huỷ âm thầm, khách không nhận email.
Áp dụng cho cả sửa/xoá toàn bộ lịch, 1 buổi riêng của lịch lặp, và xoá toàn bộ series.

⏰ <b>NHẮC LỊCH &amp; DIGEST TỰ ĐỘNG</b>
• Nhắc ~30 phút trước mỗi lịch (cả buổi lặp) — gửi vào chat này.
• 07:00 sáng: digest tất cả lịch trong ngày.
• {_c('/today')} — xem agenda hôm nay bất kỳ lúc nào.

📋 <b>QUẢN LÝ ĐẦY ĐỦ — /list</b>
• Hiện 10 lịch gần nhất, mỗi lịch 1 nút số
• Bấm số → chi tiết + nút ✏️ Sửa / 🗑 Xoá
• ✏️ → menu 6 field: giờ/ngày · thời lượng · thêm/bỏ khách · tên · nội dung
• 🗑 → confirm → huỷ Zoom + Calendar (khách nhận email huỷ)

🔎 <b>Tìm &amp; lật trang trong /list:</b>
{_cb('''/list 2                  ← trang 2 (10 lịch/trang)
/list OKRs               ← lọc theo từ khoá tên/nội dung
/list khách lan@abc.com  ← lọc theo email khách
/list tuần này
/list tuần sau | tuần trước
/list hôm nay | mai | hôm qua
/list tháng này | tháng 5 | tháng 5/2026
/list 27/4               ← ngày cụ thể
/list 27/4-4/5           ← khoảng ngày
/list OKRs 2             ← từ khoá + trang''')}

🔁 <b>LỊCH LẶP — XOÁ / SỬA 1 BUỔI RIÊNG</b>
• Xoá 1 buổi: /list → lịch lặp → 🗑 → "⊘ Chỉ 1 buổi" → chọn buổi
• Sửa 1 buổi: /list → lịch lặp → ✏️ → "🗓 Sửa 1 buổi riêng" → chọn buổi → Giờ/Thời lượng

🗓 <b>KÉO THẢ THỦ CÔNG TRÊN GOOGLE CALENDAR</b>
Chị cứ kéo thoải mái trên Calendar UI. Sau đó đồng bộ:
• {_c('/sync')} → đồng bộ lịch mới nhất
• {_c('/sync 5')} → đồng bộ lịch id=5
• Hoặc /list → bấm lịch: nếu có drift sẽ có banner ⚠️ + nút 🔄 Sync

Bot coi Calendar là nguồn đúng, update Zoom + DB theo.

📌 <b>LỆNH TỔNG HỢP</b>
• {_c('/start')}, {_c('/help')} — hướng dẫn
• {_c('/list')} — quản lý 10 lịch gần nhất
• {_c('/sync [id]')} — đồng bộ sau khi kéo thả
• {_c('/today')} — digest lịch hôm nay

⚠️ <b>Lưu ý</b>
• Chỉ chị Hải Yến nhắn được (chat_id filter).
• Lịch lặp: {_c('/sync')} chỉ đồng bộ cấp series. Kéo 1 instance riêng trên Calendar → dùng luồng "Sửa 1 buổi riêng" qua /list."""


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
