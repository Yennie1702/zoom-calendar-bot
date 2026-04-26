"""One-time script: obtain a long-lived Google OAuth refresh token.

Usage:
    python get_refresh_token.py

What it does:
    1. Opens a browser with Google's OAuth consent screen.
    2. You log in — IMPORTANT: use the Google account that OWNS the Calendar
       where you want invites to come from (nguyenthihaiyen@john.vn), NOT the
       personal account that owns the GCP project.
    3. Approve 2 scopes:
       - calendar.events — manage Google Calendar invites (existing)
       - drive.file — upload/list ONLY files this bot creates on Drive
                      (used by scripts/backup_to_drive.py daily 23h)
    4. Script prints the refresh_token — copy it into .env as GOOGLE_REFRESH_TOKEN.

Run once after adding the new Drive scope. Refresh token does not expire
unless revoked.
"""
from __future__ import annotations

import sys
from pathlib import Path

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv(Path(__file__).with_name(".env"))

import os

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
EXPECTED_ACCOUNT = os.environ.get("GOOGLE_CALENDAR_ACCOUNT", "").strip()

SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    # drive.file = chỉ touch file mà bot tự tạo (KHÔNG đọc Drive khác của chị).
    # Cho phép create, list, update, delete trên các file/folder bot upload.
    "https://www.googleapis.com/auth/drive.file",
]


def main() -> int:
    if not CLIENT_ID or not CLIENT_SECRET:
        print("❌ Missing GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET in .env")
        return 1

    print("=" * 70)
    print("🔐 Google OAuth refresh-token flow")
    print("=" * 70)
    print(f"  Expected login account: {EXPECTED_ACCOUNT or '(not set)'}")
    print("  → A browser will open. Login with THIS account, not a personal one.")
    print("=" * 70)

    client_config = {
        "installed": {
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": ["http://localhost"],
        }
    }

    flow = InstalledAppFlow.from_client_config(client_config, SCOPES)
    # access_type=offline + prompt=consent is required to receive a refresh_token
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
        open_browser=True,
    )

    if not creds.refresh_token:
        print("❌ No refresh_token returned. Re-run with a NEW Google account "
              "or revoke existing access at https://myaccount.google.com/permissions")
        return 2

    print()
    print("=" * 70)
    print("✅ Refresh token obtained.")
    print("=" * 70)
    print(f"GOOGLE_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 70)
    print("👉 Paste the line above into .env (replace the empty GOOGLE_REFRESH_TOKEN=).")
    print("   DO NOT commit .env or share this token.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
