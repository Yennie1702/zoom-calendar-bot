"""Google Calendar client — OAuth user-flow (refresh token) + Calendar API v3.

Uses the refresh token obtained from get_refresh_token.py to call Calendar API
as the work account (nguyenthihaiyen@john.vn), so invites come from that email.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from bot import config

log = logging.getLogger(__name__)

_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]


@dataclass
class CalendarEvent:
    event_id: str
    html_link: str
    calendar_id: str
    hangout_link: str = ""  # Google Meet link (set when with_meet=True)


class CalendarClient:
    def __init__(self) -> None:
        if not config.google_ready():
            raise RuntimeError(
                "Google credentials incomplete. Ensure GOOGLE_CLIENT_ID, "
                "GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN are set in .env."
            )
        self._creds = Credentials(
            token=None,
            refresh_token=config.GOOGLE_REFRESH_TOKEN,
            client_id=config.GOOGLE_CLIENT_ID,
            client_secret=config.GOOGLE_CLIENT_SECRET,
            token_uri="https://oauth2.googleapis.com/token",
            scopes=_SCOPES,
        )
        # Force an initial refresh so failures surface early
        self._creds.refresh(Request())
        self._service = build("calendar", "v3", credentials=self._creds, cache_discovery=False)

    def create_event(
        self,
        *,
        summary: str,
        description: str,
        start_local_iso: str,  # e.g. "2026-05-20T08:30:00"
        end_local_iso: str,
        attendee_emails: list[str],
        rrule: str | None = None,  # e.g. "RRULE:FREQ=WEEKLY;BYDAY=WE;COUNT=12"
        calendar_id: str | None = None,
        with_meet: bool = False,
        visibility: str | None = None,  # "default" / "public" / "private"
        notify: bool = True,
        color_id: str | None = None,    # Phase 3: Google Calendar event color (1-11)
    ) -> CalendarEvent:
        cal = calendar_id or config.GOOGLE_CALENDAR_ACCOUNT or "primary"

        body: dict = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_local_iso, "timeZone": config.TIMEZONE},
            "end": {"dateTime": end_local_iso, "timeZone": config.TIMEZONE},
            "attendees": [{"email": e} for e in attendee_emails],
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 1440},  # 1 day
                    {"method": "email", "minutes": 30},
                ],
            },
        }
        if color_id:
            body["colorId"] = color_id
        if with_meet:
            import uuid as _uuid
            body["conferenceData"] = {
                "createRequest": {
                    "requestId": _uuid.uuid4().hex,
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                },
            }
        else:
            # Suppress Meet auto-link on work flow (brief uses Zoom, not Meet)
            body["conferenceData"] = None
        if visibility:
            body["visibility"] = visibility
        if rrule:
            body["recurrence"] = [rrule]

        resp = (
            self._service.events()
            .insert(
                calendarId=cal,
                body=body,
                sendUpdates="all" if notify else "none",
                conferenceDataVersion=1 if with_meet else 0,
            )
            .execute()
        )
        log.info(
            "Created Calendar event %s on %s (meet=%s, vis=%s)",
            resp["id"], cal, with_meet, visibility or "default",
        )
        return CalendarEvent(
            event_id=resp["id"],
            html_link=resp.get("htmlLink", ""),
            calendar_id=cal,
            hangout_link=resp.get("hangoutLink", ""),
        )

    def patch_event(
        self,
        event_id: str,
        *,
        calendar_id: str | None = None,
        summary: str | None = None,
        description: str | None = None,
        start_local_iso: str | None = None,
        end_local_iso: str | None = None,
        attendee_emails: list[str] | None = None,
        rrule: str | None = None,
        clear_recurrence: bool = False,
        notify: bool = True,
    ) -> CalendarEvent:
        """PATCH a Calendar event. Only pass fields you want to change.

        Start/end must both be provided together (Google rejects partial dates).
        Passing attendee_emails replaces the full list.
        """
        cal = calendar_id or config.GOOGLE_CALENDAR_ACCOUNT or "primary"
        body: dict = {}
        if summary is not None:
            body["summary"] = summary
        if description is not None:
            body["description"] = description
        if start_local_iso is not None:
            body["start"] = {"dateTime": start_local_iso, "timeZone": config.TIMEZONE}
        if end_local_iso is not None:
            body["end"] = {"dateTime": end_local_iso, "timeZone": config.TIMEZONE}
        if attendee_emails is not None:
            body["attendees"] = [{"email": e} for e in attendee_emails]
        if rrule is not None:
            body["recurrence"] = [rrule]
        elif clear_recurrence:
            body["recurrence"] = []

        resp = (
            self._service.events()
            .patch(
                calendarId=cal,
                eventId=event_id,
                body=body,
                sendUpdates="all" if notify else "none",
                conferenceDataVersion=0,
            )
            .execute()
        )
        log.info("Patched Calendar event %s on %s (notify=%s)", resp["id"], cal, notify)
        return CalendarEvent(
            event_id=resp["id"],
            html_link=resp.get("htmlLink", ""),
            calendar_id=cal,
        )

    def delete_event(
        self, event_id: str, *, calendar_id: str | None = None, notify: bool = True
    ) -> None:
        cal = calendar_id or config.GOOGLE_CALENDAR_ACCOUNT or "primary"
        self._service.events().delete(
            calendarId=cal,
            eventId=event_id,
            sendUpdates="all" if notify else "none",
        ).execute()
        log.info("Deleted Calendar event %s on %s (notify=%s)", event_id, cal, notify)

    def get_event(
        self, event_id: str, *, calendar_id: str | None = None
    ) -> dict:
        cal = calendar_id or config.GOOGLE_CALENDAR_ACCOUNT or "primary"
        return (
            self._service.events()
            .get(calendarId=cal, eventId=event_id)
            .execute()
        )

    def list_events_in_range(
        self,
        *,
        time_min_iso: str,  # RFC3339, e.g. "2026-04-23T00:00:00+07:00"
        time_max_iso: str,
        calendar_id: str | None = None,
        max_results: int = 250,
    ) -> list[dict]:
        """List events (expanded instances) in a time window.

        Uses `singleEvents=True` so recurring series are expanded per occurrence
        — the caller gets one dict per real meeting. Cancelled instances are
        filtered out by the API when showDeleted=False (the default).
        """
        cal = calendar_id or config.GOOGLE_CALENDAR_ACCOUNT or "primary"
        items: list[dict] = []
        page_token: str | None = None
        while True:
            req = self._service.events().list(
                calendarId=cal,
                timeMin=time_min_iso,
                timeMax=time_max_iso,
                singleEvents=True,
                orderBy="startTime",
                maxResults=max_results,
                pageToken=page_token,
            )
            resp = req.execute()
            items.extend(resp.get("items", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return items

    def list_instances(
        self,
        event_id: str,
        *,
        calendar_id: str | None = None,
        max_results: int = 50,
    ) -> list[dict]:
        """List instance events of a recurring series (cancelled + active)."""
        cal = calendar_id or config.GOOGLE_CALENDAR_ACCOUNT or "primary"
        resp = (
            self._service.events()
            .instances(
                calendarId=cal,
                eventId=event_id,
                maxResults=max_results,
                showDeleted=True,
            )
            .execute()
        )
        return resp.get("items", [])

    def cancel_instance(
        self,
        instance_id: str,
        *,
        calendar_id: str | None = None,
        notify: bool = True,
    ) -> None:
        """Cancel a single occurrence of a recurring series."""
        cal = calendar_id or config.GOOGLE_CALENDAR_ACCOUNT or "primary"
        self._service.events().patch(
            calendarId=cal,
            eventId=instance_id,
            body={"status": "cancelled"},
            sendUpdates="all" if notify else "none",
        ).execute()
        log.info("Cancelled Calendar instance %s on %s (notify=%s)", instance_id, cal, notify)

    def patch_instance(
        self,
        instance_id: str,
        *,
        calendar_id: str | None = None,
        start_local_iso: str | None = None,
        end_local_iso: str | None = None,
        notify: bool = True,
    ) -> None:
        """PATCH a single occurrence's start/end (dời buổi riêng)."""
        cal = calendar_id or config.GOOGLE_CALENDAR_ACCOUNT or "primary"
        body: dict = {}
        if start_local_iso is not None:
            body["start"] = {"dateTime": start_local_iso, "timeZone": config.TIMEZONE}
        if end_local_iso is not None:
            body["end"] = {"dateTime": end_local_iso, "timeZone": config.TIMEZONE}
        if not body:
            return
        self._service.events().patch(
            calendarId=cal,
            eventId=instance_id,
            body=body,
            sendUpdates="all" if notify else "none",
        ).execute()
        log.info("Patched Calendar instance %s on %s (notify=%s)", instance_id, cal, notify)
