#!/usr/bin/env bash
# Backup memory files (markdown docs Claude tích luỹ qua các session)
# từ ~/.claude/projects/*/memory/ → data/memory_backups/<TIMESTAMP>/
#
# Run: bash scripts/backup_memory.sh
# Cron: launchd daily 23h (xem scripts/com.johnacademy.zoom-calendar-bot.backup.plist)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BACKUP_ROOT="$PROJECT_DIR/data/memory_backups"
LOG_FILE="$PROJECT_DIR/logs/backup.log"
RETENTION_DAYS=90

# Memory files của Claude Code project JOHNSPACE
SOURCE_MEMORY="$HOME/.claude/projects/-Volumes-Space-Claude-JOHNSPACE/memory"

mkdir -p "$BACKUP_ROOT" "$(dirname "$LOG_FILE")"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') [memory] $*" | tee -a "$LOG_FILE"
}

log "============================================================"
log "Memory backup start"

if [ ! -d "$SOURCE_MEMORY" ]; then
    log "WARN: source memory dir không tồn tại: $SOURCE_MEMORY"
    log "Skip memory backup."
    exit 0
fi

# Snapshot folder theo ngày
TIMESTAMP=$(date +"%Y-%m-%d_%H%M")
DEST="$BACKUP_ROOT/$TIMESTAMP"
mkdir -p "$DEST"

# Copy tất cả file .md (preserve mtime)
COUNT=$(find "$SOURCE_MEMORY" -maxdepth 2 -name "*.md" -type f | wc -l | tr -d ' ')
if [ "$COUNT" -eq 0 ]; then
    log "WARN: không tìm thấy file .md nào trong $SOURCE_MEMORY"
    rmdir "$DEST" 2>/dev/null || true
    exit 0
fi

cp -p "$SOURCE_MEMORY"/*.md "$DEST/" 2>/dev/null || true

# Tính size
SIZE=$(du -sh "$DEST" 2>/dev/null | awk '{print $1}')
log "✓ Copied $COUNT memory files → $DEST ($SIZE)"

# Cleanup snapshot > RETENTION_DAYS
DELETED=0
while IFS= read -r old; do
    rm -rf "$old"
    DELETED=$((DELETED + 1))
    log "Cleanup: removed $(basename "$old")"
done < <(find "$BACKUP_ROOT" -maxdepth 1 -mindepth 1 -type d -mtime +$RETENTION_DAYS)

if [ "$DELETED" -gt 0 ]; then
    log "Cleanup: deleted $DELETED old snapshot(s)"
fi

TOTAL=$(find "$BACKUP_ROOT" -maxdepth 1 -mindepth 1 -type d | wc -l | tr -d ' ')
log "Total memory snapshots: $TOTAL"
log "Memory backup done"
