"""Upload data/ + bot/ archives lên Google Drive folder JA-Scheduler-Backups/.

Workflow:
1. Tar.gz từng folder vào /tmp/<TS>__data.tar.gz và /tmp/<TS>__bot.tar.gz
   (exclude __pycache__, *.pyc).
2. Upload 2 file vào Drive folder `JA-Scheduler-Backups/` qua DriveClient
   (OAuth refresh token, không cần Claude session).
3. Cleanup local /tmp file.
4. Cleanup file Drive cũ > BACKUP_RETENTION_DAYS (qua API listing).

Run: venv/bin/python scripts/backup_to_drive.py
Cron: launchd daily 23h (xem com.johnacademy.zoom-calendar-bot.backup.plist)
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from bot.drive_client import DriveClient  # noqa: E402

# ── Config ──────────────────────────────────────────────────────────────────
PROJECT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_DIR / "logs"
DRIVE_FOLDER_NAME = "JA-Scheduler-Backups"
BACKUP_RETENTION_DAYS = 90

# Folders to archive (relative to PROJECT_DIR)
FOLDERS = ["data", "bot"]
EXCLUDES = ["__pycache__", "*.pyc"]


# ── Logging ─────────────────────────────────────────────────────────────────
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    handlers=[
        logging.FileHandler(LOG_DIR / "backup.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)


# ── Tar helper ──────────────────────────────────────────────────────────────
def make_archive(folder_name: str, timestamp: str) -> Path:
    """Tạo /tmp/<TS>__<folder>.tar.gz. Trả Path."""
    out_path = Path("/tmp") / f"{timestamp}__{folder_name}.tar.gz"
    cmd = ["tar", "-czf", str(out_path)]
    for excl in EXCLUDES:
        cmd.extend(["--exclude", excl])
    cmd.append(folder_name)
    subprocess.run(cmd, cwd=PROJECT_DIR, check=True)
    return out_path


# ── Cleanup ─────────────────────────────────────────────────────────────────
def cleanup_old_drive_files(client: DriveClient, folder_id: str) -> int:
    """Xoá file > BACKUP_RETENTION_DAYS trong folder Drive. Trả về số file đã xoá."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=BACKUP_RETENTION_DAYS)
    files = client.list_files_in_folder(folder_id)
    deleted = 0
    for f in files:
        try:
            created = datetime.fromisoformat(f["createdTime"].replace("Z", "+00:00"))
            if created < cutoff:
                client.delete_file(f["id"])
                deleted += 1
                log.info("Drive cleanup: removed %s (created %s)", f["name"], f["createdTime"])
        except Exception as e:  # noqa: BLE001
            log.warning("Drive cleanup skip %s: %s", f.get("name", "?"), e)
    return deleted


# ── Main ────────────────────────────────────────────────────────────────────
def main() -> int:
    log.info("=" * 60)
    log.info("Drive backup start")

    try:
        client = DriveClient()
    except Exception as e:  # noqa: BLE001
        log.exception("DriveClient init failed: %s", e)
        log.error("→ Có thể chị chưa re-OAuth với scope drive.file. "
                  "Chạy: venv/bin/python get_refresh_token.py")
        return 1

    # Đảm bảo folder root tồn tại
    folder_id = client.ensure_folder(DRIVE_FOLDER_NAME)
    log.info("Drive folder: %s (id=%s)", DRIVE_FOLDER_NAME, folder_id)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")

    # Tar + upload từng folder
    archives_to_clean = []
    success_count = 0
    for folder in FOLDERS:
        try:
            log.info("Archiving %s/...", folder)
            archive = make_archive(folder, timestamp)
            archives_to_clean.append(archive)
            size_kb = archive.stat().st_size / 1024
            log.info("  → %s (%.1f KB)", archive.name, size_kb)

            log.info("Uploading %s to Drive...", archive.name)
            uploaded = client.upload_file(
                str(archive),
                drive_name=archive.name,
                parent_id=folder_id,
                mime_type="application/gzip",
            )
            log.info("  ✓ Drive id=%s · %s", uploaded.id, uploaded.web_link)
            success_count += 1
        except Exception as e:  # noqa: BLE001
            log.exception("Failed for %s: %s", folder, e)

    # Cleanup local /tmp
    for f in archives_to_clean:
        try:
            os.unlink(f)
        except OSError:
            pass

    # Cleanup old Drive files
    try:
        deleted = cleanup_old_drive_files(client, folder_id)
        if deleted:
            log.info("Drive cleanup: deleted %d old archive(s)", deleted)
    except Exception as e:  # noqa: BLE001
        log.exception("Drive cleanup failed: %s", e)

    log.info("Drive backup done — %d/%d archive(s) uploaded", success_count, len(FOLDERS))
    return 0 if success_count == len(FOLDERS) else 1


if __name__ == "__main__":
    sys.exit(main())
