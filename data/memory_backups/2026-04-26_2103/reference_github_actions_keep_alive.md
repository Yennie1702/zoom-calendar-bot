---
name: GitHub Actions keep-alive pattern cho Render free tier
description: Cách giữ Render free tier service không ngủ bằng GitHub Actions cron (thay vì UptimeRobot hay service ngoài). Zero cost, trong existing repo.
type: reference
originSessionId: 7e420da6-5336-46b8-8aa1-d23684ddeb77
---
**Vấn đề:** Render free tier service ngủ sau 15 phút idle, request đầu tiên sau đó mất 30–60 giây wake up. Với Telegram webhook → Telegram timeout retry → user tưởng bot chết.

**Giải pháp:** GitHub Actions workflow ping endpoint mỗi 10 phút.

**Location:** `/Volumes/Space/Claude/zoom-calendar-bot/.github/workflows/keep-alive.yml` (đã deploy 2026-04-23)

**Cost:** ~10 phút/tháng actions runtime (trong quota 2000 phút free của GitHub Actions → 0đ).

**Template:**
```yaml
name: Keep Render service warm
on:
  schedule:
    - cron: "*/10 * * * *"
  workflow_dispatch:
jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - run: |
          HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 90 https://<service>.onrender.com/)
          if [ "$HTTP_CODE" = "000" ]; then exit 1; fi
```

**Gotcha khi push:** PAT mặc định thiếu scope `workflow` → `git push` bị GitHub reject khi add/sửa file trong `.github/workflows/`. 2 cách fix:
1. (Nhanh) Tạo file trực tiếp qua GitHub web UI → "Add file" → "Create new file" → đường dẫn `.github/workflows/<name>.yml`
2. Update PAT có scope `workflow` (Settings → Developer settings → Personal access tokens)

**Trigger manual test:** GitHub repo → tab **Actions** → sidebar trái chọn workflow → góc phải nút **"Run workflow"** (enabled bởi `workflow_dispatch`).

**So sánh alternatives:**
| Option | Cost | Setup | Reliability |
|---|---|---|---|
| GitHub Actions cron | 0đ | 2 phút, 1 file YAML | ~15p precision (cron drift) |
| UptimeRobot | 0đ | 5 phút, cần signup | 5p interval |
| Render Starter plan | $7/tháng | Update plan 1 click | <1s response, no cold start |
| cron-job.org | 0đ | 5 phút, cần signup | 1p interval |

**How to apply:** Khi project chạy trên Render free / fly.io free / heroku-like free tier có cold start, luôn recommend GitHub Actions keep-alive làm first choice nếu project đã có GitHub repo — zero external dependency.
