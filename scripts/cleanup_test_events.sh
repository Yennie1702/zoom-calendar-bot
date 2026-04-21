#!/usr/bin/env bash
# Cleanup script for Phase 1 C1 test events.
# Chỉ chạy khi chị Yến đã verify workflow OK.
# Xoá cả Zoom meeting + Google Calendar event cho 2 lịch test (single + recurring).

set -euo pipefail

cd "$(dirname "$0")/.."
set -a && source .env && set +a

TOKEN=$(curl -s -X POST "https://zoom.us/oauth/token?grant_type=account_credentials&account_id=$ZOOM_ACCOUNT_ID" \
  -u "$ZOOM_CLIENT_ID:$ZOOM_CLIENT_SECRET" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

echo "=== Deleting Zoom meetings ==="
for MEETING_ID in 84157697809 85834130801; do
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X DELETE \
    "https://api.zoom.us/v2/meetings/$MEETING_ID" \
    -H "Authorization: Bearer $TOKEN")
  echo "  Zoom meeting $MEETING_ID → HTTP $STATUS"
done

echo ""
echo "=== Calendar events need to be deleted via Claude Code MCP ==="
echo "  (Run in Claude Code session, these need delete_event MCP tool)"
echo "  1. Single event (22/4/2026): id=0gekd24sbl80blbllv8jrsqtoc on nguyenthihaiyen@john.vn"
echo "  2. Recurring event (29/4-20/5/2026): id=1d5biimtlrthinum3ec5bt9fm8 on nguyenthihaiyen@john.vn"

echo ""
echo "✅ Cleanup done. Remaining: manual delete 2 Calendar events via MCP."
