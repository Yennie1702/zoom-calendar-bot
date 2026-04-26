"""Google Drive client — upload/list/delete files in a target folder.

Reuses the same Google OAuth refresh token as calendar_client.py — chị Yến
chỉ cần re-OAuth 1 lần với scope `drive.file` thêm vào.

Scope `drive.file` = chỉ thấy/sửa được file mà BOT tự tạo, không đọc được
file khác trong Drive của chị. An toàn nhất.

Used by `scripts/backup_to_drive.py` for daily 23h auto-upload.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

from bot import config

log = logging.getLogger(__name__)

_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.file",
]


@dataclass
class DriveFile:
    id: str
    name: str
    mime_type: str
    web_link: str = ""


class DriveClient:
    """Tối thiểu Drive ops cần cho backup: tìm/tạo folder, upload binary file."""

    def __init__(self) -> None:
        if not config.google_ready():
            raise RuntimeError(
                "Google credentials incomplete. Ensure GOOGLE_CLIENT_ID, "
                "GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN are set in .env "
                "AND refresh token có scope drive.file (chạy lại get_refresh_token.py)."
            )
        self._creds = Credentials(
            token=None,
            refresh_token=config.GOOGLE_REFRESH_TOKEN,
            client_id=config.GOOGLE_CLIENT_ID,
            client_secret=config.GOOGLE_CLIENT_SECRET,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=_SCOPES,
        )
        # Initial refresh — fail fast nếu scope chưa đúng
        self._creds.refresh(Request())
        self._service = build("drive", "v3", credentials=self._creds, cache_discovery=False)

    # ── Folder helpers ──────────────────────────────────────────────────────
    def find_folder(self, name: str, parent_id: str | None = None) -> str | None:
        """Tìm folder theo name (+ parent). Trả về folder ID hoặc None."""
        query = (
            f"name = '{name}' "
            "and mimeType = 'application/vnd.google-apps.folder' "
            "and trashed = false"
        )
        if parent_id:
            query += f" and '{parent_id}' in parents"
        resp = self._service.files().list(
            q=query, fields="files(id, name)", pageSize=1
        ).execute()
        files = resp.get("files", [])
        return files[0]["id"] if files else None

    def ensure_folder(self, name: str, parent_id: str | None = None) -> str:
        """Trả folder ID — tạo mới nếu chưa tồn tại."""
        existing = self.find_folder(name, parent_id)
        if existing:
            return existing
        body = {
            "name": name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            body["parents"] = [parent_id]
        folder = self._service.files().create(
            body=body, fields="id, name"
        ).execute()
        log.info("Drive: created folder %s (id=%s)", name, folder["id"])
        return folder["id"]

    # ── Upload ──────────────────────────────────────────────────────────────
    def upload_file(
        self,
        local_path: str,
        *,
        drive_name: str,
        parent_id: str,
        mime_type: str = "application/octet-stream",
    ) -> DriveFile:
        """Upload binary file. Resumable upload — handles file kích cỡ bất kỳ."""
        body = {"name": drive_name, "parents": [parent_id]}
        with open(local_path, "rb") as f:
            media = MediaIoBaseUpload(
                io.BytesIO(f.read()),
                mimetype=mime_type,
                resumable=True,
                chunksize=1024 * 1024,  # 1MB chunks
            )
            req = self._service.files().create(
                body=body,
                media_body=media,
                fields="id, name, mimeType, webViewLink",
            )
            resp = None
            while resp is None:
                _, resp = req.next_chunk()
        return DriveFile(
            id=resp["id"],
            name=resp["name"],
            mime_type=resp["mimeType"],
            web_link=resp.get("webViewLink", ""),
        )

    # ── Cleanup (retention) ─────────────────────────────────────────────────
    def list_files_in_folder(self, parent_id: str) -> list[dict]:
        """List file (không phải folder) trong 1 parent."""
        query = (
            f"'{parent_id}' in parents "
            "and mimeType != 'application/vnd.google-apps.folder' "
            "and trashed = false"
        )
        resp = self._service.files().list(
            q=query,
            fields="files(id, name, createdTime, size)",
            pageSize=200,
            orderBy="createdTime desc",
        ).execute()
        return resp.get("files", [])

    def delete_file(self, file_id: str) -> None:
        self._service.files().delete(fileId=file_id).execute()
