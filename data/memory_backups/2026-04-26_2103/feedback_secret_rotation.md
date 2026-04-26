---
name: Secret rotation playbook — zero-downtime order
description: Quy trình rotate credentials cho bot Render production — thứ tự để tránh downtime, các UI gotchas của từng provider.
type: feedback
originSessionId: 7e420da6-5336-46b8-8aa1-d23684ddeb77
---
Khi rotate secrets cho service production (Render + Telegram + Google + Zoom + Turso), LUÔN theo thứ tự này để tránh downtime dài:

**Google OAuth (multi-secret support — zero-downtime):**
1. GCP Console → Clients → Add secret (tạo thứ 2) → download JSON
2. Update .env + Render ENV với secret MỚI
3. Verify service hoạt động với secret mới
4. Disable → Delete secret CŨ trên GCP Console
5. Nếu cần regen refresh token: dùng `get_refresh_token.py` với `python -u` (unbuffered) để capture token qua log. OAuth consent cần click "Nâng cao" → "Chuyển đến JA Scheduler Bot (không an toàn)" vì unverified app.

**Zoom S2S OAuth (single-secret — có downtime 1-2 phút):**
1. Marketplace → app → Regenerate Client Secret (old invalidate NGAY)
2. Paste new secret vào chat → update .env + Render
3. Save Render → auto redeploy (~90s)
4. Test bằng tạo lịch mới

**Telegram Bot Token (single — có downtime):**
1. Chat với @BotFather (conversational, không có API) → `/mybots` → chọn bot → API Token → Revoke
2. Copy new token → update .env + Render
3. Webhook URL vẫn registered (gắn với bot id, không phải token) → không cần re-register
4. Test `/start`

**Turso Auth Token (nuclear only — UI không có per-token revoke):**
1. Create new token trên dashboard → paste → update .env + Render → verify
2. Nếu muốn revoke old: chỉ có nút **Invalidate All Tokens** (kill cả mới lẫn cũ)
3. → Phải create token lần 2 → update .env + Render lần 2 → test
4. Strict mode tốn ~5 phút downtime; pragmatic mode bỏ qua revoke (token cũ valid forever nhưng unused).

**Why:** Các provider có UX rotation khác nhau — Google hỗ trợ 2 secret cùng lúc (zero-downtime), Zoom/Telegram invalidate ngay khi regenerate, Turso chỉ có nuclear invalidate. Biết trước để không mất thời gian debug.

**How to apply:** Trước khi rotate bất kỳ secret nào trên production, xác định provider thuộc loại nào. Với single-secret providers, warn user về downtime trước khi bấm Regenerate/Revoke. Chuẩn bị sẵn `.env` local + Render dashboard để update ngay khi nhận secret mới. Sau khi verify xong mới revoke old. Xóa log files chứa token (`/tmp/*.log` từ `get_refresh_token.py`) sau khi xong.
