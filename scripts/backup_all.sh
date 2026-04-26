#!/usr/bin/env bash
# Backup wrapper — chạy cả DB dump + memory snapshot.
# Được gọi bởi launchd daily 23h (xem com.johnacademy.zoom-calendar-bot.backup.plist).

set -uo pipefail  # KHÔNG -e: 1 phần fail không nên skip phần còn lại

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PYTHON_BIN="$PROJECT_DIR/venv/bin/python"
LOG_FILE="$PROJECT_DIR/logs/backup.log"

mkdir -p "$(dirname "$LOG_FILE")"
echo "" >> "$LOG_FILE"
echo "$(date '+%Y-%m-%d %H:%M:%S') [all] ════════════════════════════════════" >> "$LOG_FILE"
echo "$(date '+%Y-%m-%d %H:%M:%S') [all] backup_all.sh start" >> "$LOG_FILE"

cd "$PROJECT_DIR"

# 1. DB dump (Turso → data/backups/db_*.sql.gz)
if [ -x "$PYTHON_BIN" ]; then
    "$PYTHON_BIN" scripts/backup_db.py >> "$LOG_FILE" 2>&1
    DB_RC=$?
else
    echo "$(date '+%Y-%m-%d %H:%M:%S') [all] ERROR: $PYTHON_BIN không executable" >> "$LOG_FILE"
    DB_RC=127
fi

# 2. Memory files snapshot (~/.claude → data/memory_backups/)
bash scripts/backup_memory.sh >> "$LOG_FILE" 2>&1
MEM_RC=$?

# Summary
echo "$(date '+%Y-%m-%d %H:%M:%S') [all] DB rc=$DB_RC · memory rc=$MEM_RC" >> "$LOG_FILE"
if [ "$DB_RC" -eq 0 ] && [ "$MEM_RC" -eq 0 ]; then
    echo "$(date '+%Y-%m-%d %H:%M:%S') [all] ✓ All backups OK" >> "$LOG_FILE"
    exit 0
fi
echo "$(date '+%Y-%m-%d %H:%M:%S') [all] ⚠ Some backups failed — check log" >> "$LOG_FILE"
exit 1
