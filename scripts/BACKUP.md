# Backup workflow — JA Scheduler Bot

Backup tự động daily 23:00 local (Asia/Ho_Chi_Minh) qua macOS launchd.
Lưu **2 lớp**: local `data/` (gitignored) + Google Drive folder `JA-Scheduler-Backups/`.

## Cái gì được backup

| Loại | Nguồn | Đích | Tần suất | Giữ |
|---|---|---|---|---|
| 🔴 **DB** | Turso libSQL (events, bot_meta, external_reminders_sent) | `data/backups/db_<TS>.sql.gz` | Daily 23h | 90 ngày local |
| 🟡 **Memory files** | `~/.claude/projects/-Volumes-Space-Claude-JOHNSPACE/memory/*.md` | `data/memory_backups/<TS>/*.md` | Daily 23h | 90 ngày local |
| ☁️ **Drive mirror** | `data/` + `bot/` archives | Drive `JA-Scheduler-Backups/<TS>__data.tar.gz` + `<TS>__bot.tar.gz` | Daily 23h | 90 ngày |

## Files

- `scripts/backup_db.py` — dump 3 bảng Turso → SQL plain text → gzip
- `scripts/backup_memory.sh` — copy markdown memory files vào snapshot folder theo timestamp
- `scripts/backup_to_drive.py` — tar `data/` + `bot/` → upload Drive folder
- `bot/drive_client.py` — Drive API helper (reuse Google OAuth refresh token)
- `scripts/backup_all.sh` — wrapper gọi cả 3, chạy không -e (1 phần fail không skip phần còn lại)
- `scripts/com.johnacademy.zoom-calendar-bot.backup.plist` — launchd config

## Setup lần đầu

### 1. Local backup (đã làm)

```bash
cp scripts/com.johnacademy.zoom-calendar-bot.backup.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.johnacademy.zoom-calendar-bot.backup.plist
```

### 2. Drive backup — re-OAuth để add scope `drive.file` (LÀM 1 LẦN)

Refresh token hiện tại chỉ có scope `calendar.events`. Phải chạy lại
`get_refresh_token.py` để add `drive.file`:

```bash
cd /Volumes/Space/Claude/zoom-calendar-bot
venv/bin/python get_refresh_token.py
```

Browser sẽ mở → đăng nhập **đúng account `nguyenthihaiyen@john.vn`** →
approve 2 scopes:
- "See, edit, share, and permanently delete all your calendars" (đã có)
- "See, edit, create, and delete only the specific Google Drive files you use with this app" (mới)

Script in ra `GOOGLE_REFRESH_TOKEN=ya29...` — copy vào `.env`:

```bash
# Edit .env, replace dòng cũ
GOOGLE_REFRESH_TOKEN=<token mới in ra>
```

**Quan trọng**: token này cũng cần update trên Render dashboard
(Settings → Environment → `GOOGLE_REFRESH_TOKEN`) để bot prod cũng dùng được.
Nhưng KHÔNG bắt buộc cho backup script — backup chỉ chạy local.

Verify:

```bash
venv/bin/python scripts/backup_to_drive.py
# Expect: "Drive backup done — 2/2 archive(s) uploaded"
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
2. **Drive scope an toàn.** `drive.file` chỉ cho bot thấy/sửa file mà NÓ tự tạo
   (folder `JA-Scheduler-Backups/`), không đọc được file khác trong Drive của chị.
3. **Chưa encrypt.** DB dump chứa email khách. Drive ở account chị nên Google account
   được trust, nhưng nếu account bị compromise → leak. Có thể thêm `age` encrypt sau.
4. **Drive là private**: file upload mặc định chỉ chị đọc được (không ai khác).
5. **Log file dòng đôi.** `backup.log` hiện hiển thị mỗi dòng 2 lần do Python FileHandler +
   shell `tee` cùng append. Không ảnh hưởng correctness, có thể fix sau.

## Restore từ Drive

```bash
# Download từ Drive (qua web UI hoặc Drive Desktop)
# → /Volumes/Space/Claude/zoom-calendar-bot/restore_<TS>/

# Extract
tar -xzf <TS>__data.tar.gz   # → restores data/
tar -xzf <TS>__bot.tar.gz    # → restores bot/

# Restore DB từ data/backups/
LATEST=$(ls -t data/backups/db_*.sql.gz | head -1)
gunzip -c "$LATEST" | sqlite3 data/events_restored.db
```

## Disk footprint dự kiến

- **Local** (data/backups + memory_backups): < 5MB sau 90 ngày
- **Drive** (data + bot archives): ~80KB × 90 ngày = **~7MB** trong Drive 15GB free

## Quota Drive API

Drive API có 1B requests/ngày free. Mỗi backup = 4-5 calls (find folder, create file × 2,
list cleanup). Daily 23h = ~5 calls/ngày → không bao giờ hit quota.
