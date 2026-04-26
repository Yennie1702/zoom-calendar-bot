---
name: Calendar invite template — chị Yến style
description: Template description Google Calendar event cho JA Scheduler Bot (project zoom-calendar-bot). Khác với brief v2 gốc.
type: feedback
originSessionId: 7e420da6-5336-46b8-8aa1-d23684ddeb77
---
Dùng template sau cho description Google Calendar event (project `/Volumes/Space/Claude/zoom-calendar-bot/`):

```
Kính gửi anh/chị,

Hải Yến (John Academy) xin xác nhận lịch:
─────────────────────────────
📅 Thời gian: {Ngày, giờ}
⏱️ Thời lượng: {X} phút
🎯 Nội dung: {Nội dung}
👤 Phụ trách: Hải Yến - PM dự án
─────────────────────────────

🔗 LINK ZOOM: {zoom_link}
🆔 Meeting ID: {meeting_id}
🔑 Passcode: {passcode}

Anh chị tham gia zoom đúng giờ nhé.

Trân trọng,
Hải Yến | John Academy
```

**Why:** Chị Yến feedback sau khi xem bản demo C1 (22/4/2026 14:00 test event). Bỏ 2 câu cũ của brief v2: "Vui lòng vào trước 2-3 phút để được admit vào phòng." + "Có gì thay đổi anh/chị nhắn em sớm nhé!". Không hiển thị SĐT (chị yêu cầu bỏ).

**How to apply:** Khi code Calendar client cho bot (Phase 2 C2), dùng đúng template này làm default description. KHÔNG add dòng "Vui lòng vào trước 2-3 phút" hoặc SĐT trừ khi chị yêu cầu lại sau.
