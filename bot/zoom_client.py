"""Zoom API client — Server-to-Server OAuth with auto-refresh."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import requests

from bot import config

log = logging.getLogger(__name__)

_OAUTH_URL = "https://zoom.us/oauth/token"
_API_BASE = "https://api.zoom.us/v2"

# Zoom weekly_days numbering: 1=Sun, 2=Mon, 3=Tue, 4=Wed, 5=Thu, 6=Fri, 7=Sat
_WEEKDAY_ZOOM = {"MO": 2, "TU": 3, "WE": 4, "TH": 5, "FR": 6, "SA": 7, "SU": 1}
# Google RRULE BYDAY codes (we use these internally too)
WEEKDAY_RRULE = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")


@dataclass
class ZoomMeeting:
    meeting_id: int
    topic: str
    join_url: str
    passcode: str
    host_email: str
    start_time_utc: str  # ISO 8601, e.g. "2026-04-22T07:00:00Z"
    duration_min: int
    is_recurring: bool
    occurrences: list[dict[str, Any]]  # [{start_time, occurrence_id, ...}]


class ZoomClient:
    def __init__(self) -> None:
        self._access_token: str | None = None
        self._expires_at: float = 0.0

    def _token(self) -> str:
        # Refresh 60s before expiry
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        log.info("Refreshing Zoom S2S OAuth token")
        r = requests.post(
            _OAUTH_URL,
            params={
                "grant_type": "account_credentials",
                "account_id": config.ZOOM_ACCOUNT_ID,
            },
            auth=(config.ZOOM_CLIENT_ID, config.ZOOM_CLIENT_SECRET),
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        self._access_token = data["access_token"]
        self._expires_at = time.time() + int(data.get("expires_in", 3600))
        return self._access_token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
        }

    def create_meeting(
        self,
        *,
        topic: str,
        start_local_iso: str,  # e.g. "2026-05-20T08:30:00" (no TZ, naive local)
        duration_min: int,
        agenda: str = "",
        recurrence: dict[str, Any] | None = None,
    ) -> ZoomMeeting:
        """Create a scheduled or recurring Zoom meeting under the S2S account owner.

        If `recurrence` is None → type=2 (scheduled one-time).
        If `recurrence` is dict → type=8 (recurring with fixed time).
        Recurrence dict format (Zoom native):
            {"type": 2, "repeat_interval": 1, "weekly_days": "4", "end_times": 12}
        """
        body: dict[str, Any] = {
            "topic": topic,
            "type": 8 if recurrence else 2,
            "start_time": start_local_iso,
            "duration": duration_min,
            "timezone": config.TIMEZONE,
            "agenda": agenda,
            "settings": {
                # Khách join trước 15 phút mà KHÔNG cần host start
                "join_before_host": True,
                "jbh_time": 15,                # 0=anytime / 5 / 10 / 15
                # Khách auto vào, KHÔNG cần host admit
                "waiting_room": False,
                "approval_type": 2,            # 2=No registration required
                # Mute mặc định khi vào (chị Yến set sẵn từ Phase 1, giữ nguyên)
                "mute_upon_entry": True,
                "audio": "both",
                # Auto record lên Zoom Cloud — settings format (speaker/gallery/
                # shared screen/audio only/transcript/chat) bật ở Zoom UI
                # account-level Settings → Recording → Cloud Recording.
                "auto_recording": "cloud",
            },
        }
        if recurrence:
            body["recurrence"] = recurrence

        r = requests.post(
            f"{_API_BASE}/users/me/meetings",
            headers=self._headers(),
            json=body,
            timeout=20,
        )
        if r.status_code >= 400:
            log.error("Zoom create_meeting failed %s: %s", r.status_code, r.text)
            r.raise_for_status()
        d = r.json()
        # Recurring (type=8) responses omit top-level start_time / duration;
        # fall back to first occurrence (or our input, which we know).
        occurrences = d.get("occurrences", [])
        start_time_utc = d.get("start_time") or (
            occurrences[0]["start_time"] if occurrences else start_local_iso
        )
        resp_duration = d.get("duration")
        if resp_duration is None and occurrences:
            resp_duration = occurrences[0].get("duration")
        return ZoomMeeting(
            meeting_id=int(d["id"]),
            topic=d.get("topic", topic),
            join_url=d["join_url"],
            passcode=d.get("password", ""),
            host_email=d.get("host_email", ""),
            start_time_utc=start_time_utc,
            duration_min=int(resp_duration) if resp_duration is not None else duration_min,
            is_recurring=d.get("type") == 8,
            occurrences=occurrences,
        )

    def update_meeting(
        self,
        meeting_id: int | str,
        *,
        topic: str | None = None,
        start_local_iso: str | None = None,
        duration_min: int | None = None,
        agenda: str | None = None,
        recurrence: dict | None = None,
    ) -> None:
        """PATCH a Zoom meeting. Only pass fields you want to change.

        For recurring meetings, passing start_local_iso / recurrence updates
        the whole series.
        """
        body: dict = {}
        if topic is not None:
            body["topic"] = topic
        if start_local_iso is not None:
            body["start_time"] = start_local_iso
            body["timezone"] = config.TIMEZONE
        if duration_min is not None:
            body["duration"] = duration_min
        if agenda is not None:
            body["agenda"] = agenda
        if recurrence is not None:
            body["recurrence"] = recurrence
        if not body:
            return
        r = requests.patch(
            f"{_API_BASE}/meetings/{meeting_id}",
            headers=self._headers(),
            json=body,
            timeout=20,
        )
        if r.status_code not in (204, 200):
            log.error("Zoom update_meeting failed %s: %s", r.status_code, r.text)
            r.raise_for_status()

    def delete_meeting(self, meeting_id: int | str) -> None:
        r = requests.delete(
            f"{_API_BASE}/meetings/{meeting_id}",
            headers=self._headers(),
            timeout=15,
        )
        if r.status_code not in (204, 404):
            log.error("Zoom delete_meeting failed %s: %s", r.status_code, r.text)
            r.raise_for_status()

    def get_meeting(self, meeting_id: int | str) -> dict:
        """Fetch full meeting detail (includes `occurrences` for recurring)."""
        r = requests.get(
            f"{_API_BASE}/meetings/{meeting_id}",
            headers=self._headers(),
            timeout=15,
        )
        r.raise_for_status()
        return r.json()

    def delete_occurrence(self, meeting_id: int | str, occurrence_id: str) -> None:
        """Cancel one buổi of a recurring series without touching the rest."""
        r = requests.delete(
            f"{_API_BASE}/meetings/{meeting_id}",
            headers=self._headers(),
            params={"occurrence_id": occurrence_id},
            timeout=15,
        )
        if r.status_code not in (204, 404):
            log.error("Zoom delete_occurrence failed %s: %s", r.status_code, r.text)
            r.raise_for_status()

    def update_occurrence(
        self,
        meeting_id: int | str,
        occurrence_id: str,
        *,
        start_local_iso: str | None = None,
        duration_min: int | None = None,
        agenda: str | None = None,
    ) -> None:
        """PATCH a single occurrence in a recurring series."""
        body: dict = {}
        if start_local_iso is not None:
            body["start_time"] = start_local_iso
            body["timezone"] = config.TIMEZONE
        if duration_min is not None:
            body["duration"] = duration_min
        if agenda is not None:
            body["agenda"] = agenda
        if not body:
            return
        r = requests.patch(
            f"{_API_BASE}/meetings/{meeting_id}",
            headers=self._headers(),
            params={"occurrence_id": occurrence_id},
            json=body,
            timeout=20,
        )
        if r.status_code not in (204, 200):
            log.error(
                "Zoom update_occurrence failed %s: %s", r.status_code, r.text
            )
            r.raise_for_status()


def build_weekly_recurrence(rrule_byday: str, count: int) -> dict[str, Any]:
    """Translate Google-style BYDAY (e.g. 'WE') + COUNT to Zoom recurrence dict."""
    day_code = _WEEKDAY_ZOOM.get(rrule_byday.upper())
    if day_code is None:
        raise ValueError(f"Unknown BYDAY code: {rrule_byday!r}")
    return {
        "type": 2,
        "repeat_interval": 1,
        "weekly_days": str(day_code),
        "end_times": int(count),
    }
