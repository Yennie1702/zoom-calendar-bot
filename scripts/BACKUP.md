# Backup workflow — JA Scheduler Bot

Backup tự động daily 23:00 local (Asia/Ho_Chi_Minh) qua macOS launchd.
Tất cả file lưu trong `data/` (đã gitignored, không leak email khách).

## Cái gì được backup

| Loại | Nguồn | Đích | Tần suất | Giữ |
|---|---|---|---|---|
| 🔴 **DB** | Turso libSQL (events, bot_meta, external_reminders_sent) | `data/backups/db_<TS>.sql.gz` | Daily 23h | 90 ngày |
| 🟡 **Memory files** | `~/.claude/projects/-Volumes-Space-Claude-JOHNSPACE/memory/*.md` | `data/memory_backups/<TS>/*.md` | Daily 23h | 90 ngày |

## Files

- `scripts/backup_db.py` — dump 3 bảng Turso → SQL plain text → gzip
- `scripts/backup_memory.sh` — copy markdown memory files vào snapshot folder theo timestamp
- `scripts/backup_all.sh` — wrapper gọi cả 2, chạy không -e (1 phần fail không skip phần còn lại)
- `scripts/com.johnacademy.zoom-calendar-bot.backup.plist` — launchd config

## Setup lần đầu (đã làm — chỉ tham khảo)

```bash
cp scripts/com.johnacademy.zoom-calendar-bot.backup.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.johnacademy.zoom-calendar-bot.backup.plist
```

## Operate

```bash
# Status
launchctl list | grep zoom-calendar-bot
# Output: "-  <exit_code>  com.johnacademy.zoom-calendar-bot.backup"
# - = chưa run từ load gần nhất; số = exit code lần cuối (0 = OK)

# Trigger ngay (test)
launchctl start com.johnacademy.zoom-calendar-bot.backup

# Xem log
tail -50 logs/backup.log

# List backups
ls -lh data/backups/
ls -lh data/memory_backups/

# Restart sau khi sửa plist
launchctl unload ~/Library/LaunchAgents/com.johnacademy.zoom-calendar-bot.backup.plist
launchctl load   ~/Library/LaunchAgents/com.johnacademy.zoom-calendar-bot.backup.plist

# Uninstall hoàn toàn
launchctl unload ~/Library/LaunchAgents/com.johnacademy.zoom-calendar-bot.backup.plist
rm              ~/Library/LaunchAgents/com.johnacademy.zoom-calendar-bot.backup.plist
```

## Restore DB

```bash
# Pick file backup (mới nhất chẳng hạn)
LATEST=$(ls -t data/backups/db_*.sql.gz | head -1)

# Option A: restore vào local SQLite (test)
gunzip -c "$LATEST" | sqlite3 data/events_restored.db

# Option B: restore vào Turso (production — cần Turso CLI)
gunzip -c "$LATEST" | turso db shell ja-scheduler-bot

# Option C: restore vào Turso qua libsql_client (Python)
gunzip -c "$LATEST" | venv/bin/python -c "
import sys
from bot.db import _conn
sql = sys.stdin.read()
with _conn() as c:
    for stmt in sql.split(';'):
        s = stmt.strip()
        if s and not s.startswith('--'):
            c.execute(s)
    c.commit()
"
```

## Restore memory files

```bash
# Copy snapshot mới nhất về vị trí gốc
LATEST=$(ls -td data/memory_backups/*/ | head -1)
cp -p "$LATEST"*.md ~/.claude/projects/-Volumes-Space-Claude-JOHNSPACE/memory/
```

## Caveats

1. **Máy tắt qua 23h → skip ngày đó.** launchd `StartCalendarInterval` trên macOS:
   khi máy sleep đúng 23h, launchd catch-up khi máy wake (trong giờ chị làm) — vẫn chạy
   nhưng có thể là 8-9h sáng hôm sau. OK vì daily cadence không cần precise.
2. **Chưa encrypt.** DB dump chứa email khách. Hiện tại đặt trong `data/` (gitignored,
   trên ổ Space của chị). Nếu chị muốn upload lên Google Drive sau này, cần `age` encrypt
   trước (chưa setup, sẽ làm khi chị yêu cầu).
3. **Chỉ backup local.** Nếu ổ Space hỏng = mất hết. Để triple-redundancy, sau này:
   - Sync `data/backups/` qua Google Drive Desktop → cloud copy
   - Hoặc thêm step push vào GitHub private repo (qua Actions ngược)
4. **Log file dòng đôi.** `backup.log` hiện hiển thị mỗi dòng 2 lần do Python FileHandler +
   shell `tee` cùng append. Không ảnh hưởng correctness, có thể fix sau nếu khó đọc.

## Disk footprint dự kiến

- DB dump ~3KB/file × 90 ngày = ~270KB
- Memory snapshot ~40KB × 90 ngày = ~3.6MB
- **Tổng < 5MB sau 90 ngày** — không lo full ổ.
