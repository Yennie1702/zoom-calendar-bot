---
name: Use case library — formatting convention
description: How to format /usecase registrations posted to company KV API so they render readably on claude.maixuandat.com/usecase
type: feedback
originSessionId: ed8f7ffa-169e-4eaf-ab81-e71d81a64b90
---
Khi POST lên `/api/usecase`, các field dạng long text **phải có `\n` xuống dòng** trong JSON — API render raw, không auto-wrap. Bỏ sót newline → block chữ dính liền, chị Yến sẽ bắt sửa.

**Why:** Web thư viện hiển thị từng field như plain text với whitespace preserve. Một đoạn không xuống dòng đọc mệt, chị đã confirm cần xuống dòng rõ ràng.

**How to apply:**

Các field cần format với `\n`:
- `giai_phap_ai` — numbered list 7 bước, mỗi bước 1 dòng, cách đoạn mở/đóng bằng `\n\n`
- `ket_qua_so_lieu` — mỗi kết quả 1 dòng với prefix `✓ `
- `duc_ket_kinh_nghiem` — chia section "Bẫy:" và "Lưu ý:", mỗi ý 1 dòng với `- ` prefix, cách section bằng `\n\n`
- `huong_dan` — numbered list, mỗi bước cách nhau `\n\n` (để thoáng dễ đọc)

File local `.md` dùng markdown thuần (## heading, **bold**, list `-`, `✓`) — không vấn đề vì markdown render.

Viết JSON bằng Write tool vào file tạm rồi `curl --data-binary @file.json` — tránh escape hell khi chèn trực tiếp vào `-d` inline.
