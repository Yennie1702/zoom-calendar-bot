# JA Scheduler Bot — Hướng dẫn cho team

Chào Hương + Thuỳ 👋

Bot này tên **@JA_Scheduler_bot** (chị Hải Yến đã thiết kế). Mục đích: **mời chị/bạn tạo + quản lý lịch họp Zoom + Google Calendar nhanh gọn qua chat Telegram** — không cần mở Zoom hay Calendar UI.

Đây là hướng dẫn để team test + dùng hàng ngày.

---

## 1. Bot làm được gì?

| Việc | Trước đây | Với bot |
|---|---|---|
| Tạo lịch họp khách + gửi Zoom | Mở Zoom → tạo meeting → copy link → mở Calendar → tạo event → paste link → invite khách | Gõ 5 dòng vào chat → bot tự làm hết |
| Mời khách | Gõ tay từng email | Gõ tên (`Toàn, Hương`) → bot tra sổ thay tên thành email |
| Sửa giờ / huỷ lịch | Vào Calendar tìm event → sửa | Gõ `sửa giờ 15h #5` hoặc bấm nút |
| Nhắc lịch | Calendar gửi email | Bot gửi tin Telegram 30 phút trước, có link Zoom click vào ngay |

Bot chỉ phục vụ **2 chỗ**:
- **Chat 1-1** với chị Hải Yến
- **Group "JA Scheduler Team"** (Hương + Thuỳ + chị Yến)

---

## 2. Bước đầu tiên — gõ `/whoami` xác nhận identity

Trong group **JA Scheduler Team**, gõ:

```
/whoami
```

Bot phải reply với 1 message gồm:
- 👤 User ID của bạn (số dài ~10 chữ số)
- 📛 Tên Telegram
- 📍 Chat ID = `-5136308743` (group team)
- ⏱️ Thời gian hiện tại VN

Nếu bot reply OK → bạn đã được whitelist. Nếu bot không phản hồi gì → báo chị Yến để add user_id của bạn vào hệ thống.

---

## 3. Tạo lịch họp — cú pháp chuẩn

Gõ vào chat **JA Scheduler Team** theo format:

```
Tạo lịch "Tên cuộc họp":
- Thời gian: 30/4/2026 14:00
- Thời lượng: 60 phút
- Nội dung: Mô tả ngắn về buổi họp
- Khách: lan@abc.com, Toàn
```

Lưu ý:
- **Tên cuộc họp** trong dấu ngoặc kép `"..."`
- **Thời gian**: `dd/mm/yyyy HH:MM` hoặc `HH:MM dd/mm/yyyy`
- **Thời lượng**: `30 phút`, `1 tiếng`, `90 phút`...
- **Khách**: dùng email đầy đủ (`abc@x.com`) **hoặc** tên member trong sổ (`Toàn`, `Hương`, `Thuỳ`...) — bot tự tra ra email
- Mời cả team: `Khách: /all`

### Sau khi gõ → bot làm gì

1. Bot reply preview: "📋 Em hiểu lệnh như sau, chị xác nhận giúp em..."
2. Bạn bấm **✅ Xác nhận tạo** (hoặc **❌ Huỷ**)
3. Bot tự tạo Zoom meeting + Google Calendar event + invite khách qua email
4. Bot báo vào group: "📅 Lịch mới được tạo bởi {tên bạn} ({team})"

### Tạo lịch lặp hàng tuần

```
Tạo lịch "Mentor MBOs JoyClub":
- Thời gian: 8h30 sáng thứ 4 hàng tuần trong 12 tuần liên tiếp bắt đầu từ 6/5/2026
- Thời lượng: 90 phút
- Nội dung: Coaching MBOs cho team JoyClub
- Khách: Toàn
```

Bot tạo 12 buổi liên tiếp với cùng 1 link Zoom (Zoom recurring meeting).

---

## 4. Xem lịch — `/mylist` + `/list`

| Lệnh | Hiện gì |
|---|---|
| `/mylist` | **Lịch chính bạn tạo** (dù là Hương, Thuỳ, hay Yến) |
| `/list` | Tất cả lịch group (chỉ chị Yến — Admin — được dùng) |

Mỗi dòng có nút số 1-10 → bấm để xem chi tiết → có nút **✏️ Sửa** / **🗑 Xoá**.

### Lọc / tìm

```
/mylist tuần này        ← lịch tuần hiện tại
/mylist tuần sau
/mylist 30/4            ← ngày cụ thể
/mylist OKRs            ← lọc từ khoá
/mylist khách lan@abc.com
```

---

## 5. Sửa lịch — cách nhanh

Gõ thẳng vào chat (không cần mở /mylist):

```
sửa giờ 15h30 #5             ← đổi giờ lịch id=5
sửa thời lượng 45 phút #5
sửa tên "Tên mới" #5
thêm khách Hương #5
bỏ khách lan@abc.com #5
xoá lịch #5
```

(Lấy `#id` từ /mylist)

Hoặc qua menu:
- /mylist → bấm số → bấm **✏️ Sửa** → chọn field → gõ giá trị mới → confirm

### Quyền hạn

| Hành động | Member (Hương/Thuỳ) | Admin (Yến) |
|---|---|---|
| Tạo lịch | ✅ | ✅ |
| Sửa/xoá lịch chính mình tạo | ✅ | ✅ |
| Sửa/xoá lịch người khác | ❌ (bot từ chối) | ✅ |

---

## 6. Sổ thành viên — gõ tên thay email

Bot có sổ tên + email sẵn 10 người trong team. Khi gõ Khách, có thể dùng:

```
- Khách: Toàn, Hương, Oanh, abc@external.com
```

Bot tự tra sổ → resolve thành email đầy đủ.

```
/members              ← xem cả 10 người trong sổ
```

Add/sửa sổ chỉ chị Yến làm được.

---

## 7. Bot tự nhắc lịch

| Sự kiện | Bot làm gì |
|---|---|
| 30 phút trước mỗi lịch | Gửi 1 message vào group "⏰ Còn 30 phút đến lịch..." kèm link Zoom + người phụ trách |
| Tạo lịch mới | "📅 Lịch mới được tạo bởi..." vào group |
| Sửa giờ / xoá lịch | "🔄 Cập nhật" / "🗑 Xoá" vào group |

→ Cả team luôn biết ai tạo gì, lúc nào, ở đâu.

Nếu muốn **không nhận nhắc cho 1 lịch cụ thể**, chỉ cần mute thông báo Telegram của group khi không cần.

---

## 8. Test cho team — 3 bài nhỏ

Mỗi người làm 3 bài này trong group **JA Scheduler Team** để verify bot hoạt động đúng:

### Bài 1 — `/whoami`
Gõ `/whoami` → bot reply user_id + chat_id.

### Bài 2 — Tạo 1 lịch test
```
Tạo lịch "Test bot - {tên bạn}":
- Thời gian: 5/5/2026 10:00
- Thời lượng: 30 phút
- Nội dung: Test bot Phase 3
- Khách: lan@abc.com
```
→ Preview hiện ra, bấm ✅ Xác nhận tạo.

Verify:
- Email **gmail cá nhân** của bạn (`ngoquynhhuong@john.vn` / `vukimthuy@john.vn`) nhận 1 invite từ Calendar
- Group nhận 1 message "📅 Lịch mới được tạo bởi Hương/Thuỳ ({team})"
- Lịch xuất hiện trên Calendar app cá nhân của bạn

### Bài 3 — Sửa rồi xoá lịch test
- /mylist → tìm lịch vừa tạo → bấm số → bấm ✏️ Sửa → chọn 🕐 Giờ/ngày → gõ `11:00 5/5/2026` → confirm
- Group nhận message "🔄 Cập nhật"
- Sau đó bấm 🗑 Xoá → confirm
- Group nhận message "🗑 Xoá"

Khi 3 bài đều ✓ → bạn đã sẵn sàng dùng bot cho công việc thật.

---

## 9. Khi gặp lỗi

- Bot không phản hồi → đợi 30-60 giây (lần đầu trong ngày bot có thể "ngủ" nếu không ai dùng lâu, sẽ tự "thức" sau ping đầu).
- Bot reply lỗi parse "Em không hiểu giờ" → kiểm tra format `dd/mm/yyyy HH:MM`.
- Bot reply "Bạn chỉ thao tác được lịch chính mình tạo" → đó là quy tắc — gõ `/mylist` xem lịch của mình.
- Bot reply "Chị chưa được cấp quyền dùng bot" → nhắn chị Yến.

Mọi vấn đề khác → ping chị Yến trong group.

---

## 10. Một số lệnh hay dùng (cheat sheet)

```
/whoami                — xem identity của mình
/mylist                — lịch của mình
/mylist tuần này       — lịch tuần này
/list                  — tất cả lịch group (Admin only)
/today                 — lịch hôm nay
/members               — sổ thành viên
/help                  — hướng dẫn đầy đủ
/sync 5                — đồng bộ lịch id=5 nếu kéo thả trên Calendar
huỷ                    — bỏ trạng thái chờ confirm
```

---

*Có gì chưa rõ ping chị Yến trong group nhé. Chúc team dùng bot mượt 🚀*
