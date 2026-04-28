#!/usr/bin/env bash
# One-shot: upload 13 GitHub Secrets từ .env lên GitHub Actions secrets.
#
# Run: bash scripts/upload_secrets.sh
#
# Cần `gh` CLI đã login (gh auth status). Đọc value từng key trong .env
# rồi gọi `gh secret set`.

set -uo pipefail

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "❌ Không tìm thấy .env trong $(pwd)"
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "❌ Chưa cài gh CLI. brew install gh && gh auth login"
  exit 1
fi

KEYS=(
  TELEGRAM_BOT_TOKEN
  TELEGRAM_ALLOWED_CHAT_ID
  ZOOM_ACCOUNT_ID
  ZOOM_CLIENT_ID
  ZOOM_CLIENT_SECRET
  GOOGLE_CLIENT_ID
  GOOGLE_CLIENT_SECRET
  GOOGLE_REFRESH_TOKEN
  GOOGLE_CALENDAR_ACCOUNT
  TURSO_DATABASE_URL
  TURSO_AUTH_TOKEN
  CONTACT_NAME
  CONTACT_TITLE
)

ok=0
fail=0

for key in "${KEYS[@]}"; do
  value=$(grep -E "^${key}=" .env | head -1 | cut -d'=' -f2- | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")
  if [[ -z "$value" ]]; then
    echo "  ⚠️  $key: NOT FOUND in .env (skip)"
    fail=$((fail+1))
    continue
  fi
  # Pass via -b flag (string), KHÔNG dùng --body - (stdin) — stdin path
  # đôi khi truncate trên 1 số gh CLI versions (chị Yến gặp ngày 2026-04-28).
  if gh secret set "$key" --body "$value" >/dev/null 2>&1; then
    echo "  ✅ $key (len=${#value})"
    ok=$((ok+1))
  else
    echo "  ❌ $key FAILED"
    fail=$((fail+1))
  fi
done

echo ""
echo "Done: $ok ✅  /  $fail ❌"
echo ""
echo "Verify:  gh secret list"
