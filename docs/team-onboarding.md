# JA Scheduler Bot — Hướng dẫn cho team

Chào Hương + Thuỳ 👋

Bot **@JA_Scheduler_bot** giúp team mình **tạo + quản lý lịch họp Zoom + Google Calendar nhanh gọn qua Telegram** — không cần mở Zoom hay Calendar UI.

Đây là hướng dẫn cho team mình.

---

## 1. Bot làm được gì?

| Việc | Trước đây | Với bot |
|---|---|---|
| Tạo lịch họp Zoom + invite khách | Mở Zoom → tạo meeting → copy link → mở Calendar → tạo event → paste link → invite từng khách | Gõ 5 dòng vào group → bot làm hết |
| Mời khách | Gõ tay từng email (dễ sai) | Gõ tên trong sổ (`Toàn`, `Hương`...) → bot tự tra email |
| Sửa giờ / huỷ lịch | Vào Calendar tìm event → sửa | Gõ `sửa giờ 15h #5` hoặc bấm nút trong /mylist |
| Nhắc lịch | Calendar gửi email | Bot gửi tin Telegram 30 phút trước, có link Zoom click vào ngay |

## 2. Bot chỉ phục vụ trong group "JA Scheduler Team"

⚠️ **KHÔNG chat 1-1 với bot** — bot sẽ từ chối.

Mọi thao tác đều thực hiện trong group **JA Scheduler Team**.

---

## 3. Bước đầu tiên — gõ `/whoami` xác nhận

Trong group, gõ:

```
/whoami
```

Bot reply 1 message gồm:
- 👤 User ID của bạn
- 📛 Tên Telegram
- 📍 Chat ID = `-5136308743` (group team)
- ⏱️ Thời gian VN

✅ Nếu bot reply OK → bạn đã được whitelist, sẵn sàng dùng.  
❌ Nếu bot không phản hồi → ping chị Yến để add user_id của bạn.

---

## 4. Tạo lịch họp — cú pháp chuẩn

Gõ trong group:

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
4. Bot reply: "✅ Đã tạo xong: ..." kèm link Zoom + tên người tạo

### Tạo lịch lặp hàng tuần

```
Tạo lịch "Mentor MBOs JoyClub":
- Thời gian: 8h30 sáng thứ 4 hàng tuần trong 12 tuần liên tiếp bắt đầu từ 6/5/2026
- Thời lượng: 90 phút
- Nội dung: Coaching MBOs cho team JoyClub
- Khách: Toàn
```

→ Bot tạo 12 buổi với cùng 1 link Zoom (Zoom recurring meeting).

---

## 5. Xem lịch — `/mylist`

```
/mylist                — lịch của bạn
/mylist tuần này       — lịch tuần hiện tại
/mylist tuần sau
/mylist 30/4           — ngày cụ thể
/mylist OKRs           — lọc từ khoá trong tên/nội dung
/mylist khách lan@abc.com
```

Mỗi dòng có nút số 1-10 → bấm để xem chi tiết → có nút **✏️ Sửa** / **🗑 Xoá**.

⚠️ **`/mylist` chỉ hiện lịch chính bạn tạo**, không thấy lịch người khác.

---

## 6. Sửa lịch — cách nhanh

Gõ thẳng trong group (không cần mở /mylist):

```
sửa giờ 15h30 #5             ← đổi giờ lịch id=5
sửa thời lượng 45 phút #5
sửa tên "Tên mới" #5
thêm khách Hương #5
bỏ khách lan@abc.com #5
xoá lịch #5
```

(Lấy `#id` từ /mylist)

Hoặc qua menu: /mylist → bấm số → bấm **✏️ Sửa** → chọn field → gõ giá trị mới → confirm.

⚠️ **Bạn chỉ sửa/xoá được lịch chính bạn tạo.** Nếu thử thao tác lịch của người khác, bot sẽ từ chối: *"Bạn chỉ thao tác được lịch chính bạn tạo."*

---

## 7. Sổ thành viên — gõ tên thay email

Bot có sổ tên + email sẵn của 10 người trong team. Khi gõ Khách, bạn có thể dùng:

```
- Khách: Toàn, Hương, Oanh, abc@external.com
```

Bot tự tra sổ → resolve thành email đầy đủ.

```
/members              ← xem cả 10 người trong sổ
```

⚠️ Sổ này do **chị Yến (Admin)** thêm/sửa. Nếu bạn cần thêm 1 thành viên/đối tác mới vào sổ, ping chị Yến.

---

## 8. Đồng bộ với Calendar UI — `/sync`

Nếu bạn kéo thả event trên Google Calendar UI (đổi giờ trực tiếp trên web) → bot không tự biết. Báo bot đồng bộ:

```
/sync 5             ← đồng bộ lịch id=5
```

Bot coi Calendar là **nguồn đúng**, update Zoom + DB theo Calendar.

---

## 9. Bot tự nhắc lịch

| Sự kiện | Bot làm gì |
|---|---|
| 30 phút trước mỗi lịch | Gửi 1 message vào group "⏰ Còn 30 phút..." kèm link Zoom + tag người phụ trách |
| Tạo lịch mới | Reply confirm "✅ Đã tạo xong..." có tag người tạo |
| Sửa giờ / xoá lịch | Reply có tag người sửa/xoá + diff thay đổi |

→ Cả team luôn biết ai tạo gì, lúc nào.

Nếu muốn **không nhận nhắc**, mute thông báo Telegram của group.

---

## 10. Xem lịch hôm nay — `/today`

```
/today
```

Bot trả về digest tất cả lịch trong group hôm nay (sort theo giờ).

---

## 11. Cheat sheet — lệnh hay dùng

```
/whoami                — xem identity của mình
/mylist                — lịch của mình
/mylist tuần này       — lịch tuần này
/today                 — lịch group hôm nay
/members               — xem sổ thành viên
/sync 5                — đồng bộ lịch id=5
/help                  — hướng dẫn đầy đủ
huỷ                    — bỏ trạng thái chờ confirm
```

### Tạo + sửa nhanh:

```
Tạo lịch "Tên":            ← format đầy đủ ở section 4
- ...

sửa giờ 15h30 #5           ← đổi giờ
thêm khách Toàn #5         ← thêm khách
xoá lịch #5                ← xoá

Khách: /all                ← mời cả team
Khách: Toàn, Hương         ← dùng tên thay email
```

---

## 12. Test cho team — 3 bài nhỏ

Mỗi người làm 3 bài này trong group **JA Scheduler Team** để verify bot hoạt động đúng:

### Bài 1 — `/whoami`
Gõ `/whoami` → bot reply user_id + chat_id.

### Bài 2 — Tạo 1 lịch test
```
Tạo lịch "Test bot - {tên bạn}":
- Thời gian: 5/5/2026 10:00
- Thời lượng: 30 phút
- Nội dung: Test bot
- Khách: lan@abc.com
```

→ Preview hiện ra → bấm ✅ Xác nhận tạo.

Verify:
- Email công ty của bạn (`ngoquynhhuong@john.vn` / `vukimthuy@john.vn`) nhận 1 invite từ Calendar
- Bot reply confirm có tag tên bạn ({team})
- Lịch xuất hiện trên Calendar app cá nhân của bạn

### Bài 3 — Sửa rồi xoá lịch test
- /mylist → bấm số lịch vừa tạo → bấm ✏️ Sửa → chọn 🕐 Giờ/ngày → gõ `11:00 5/5/2026` → confirm
- Bot reply có tag "Sửa bởi" + diff giờ
- Sau đó bấm 🗑 Xoá → confirm
- Bot reply có tag "Xoá bởi"
- Verify: event biến mất khỏi Calendar

✅ 3 bài đều OK → bạn đã sẵn sàng dùng bot cho công việc thật.

---

## 13. Khi gặp lỗi

| Lỗi | Cách xử lý |
|---|---|
| Bot không phản hồi sau 30s | Đợi 1 phút (lần đầu trong ngày bot có thể "ngủ" — sẽ tự "thức") |
| Bot reply "Em không hiểu giờ" | Kiểm tra format `dd/mm/yyyy HH:MM` |
| Bot reply "Bạn chỉ thao tác được lịch chính bạn tạo" | Đó là quy tắc — gõ `/mylist` xem lịch của bạn |
| Bot reply "Tên 'Linh' khớp 2 người trong sổ" | Tên trong sổ trùng nhau → gõ rõ hơn (vd "Linh A" thay vì "Linh") hoặc dùng email đầy đủ |
| Bot reply "Em không tìm thấy 'Tên'" | Tên không có trong sổ → dùng email đầy đủ, hoặc nhờ chị Yến add vào sổ |

Mọi vấn đề khác → ping chị Yến trong group.

---

*Có gì chưa rõ ping chị Yến nhé. Chúc team dùng bot mượt 🚀*
