"""Shared Google OAuth setup.

Calendar reading + Gmail sending share one token. The first time either is used,
the browser flow runs once and writes token.json — subsequent runs reuse it.

If you change SCOPES, delete token.json so the next run re-prompts with the new scopes.
"""
from __future__ import annotations

import datetime as dt
import html
import os
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"
URL_RE = re.compile(r"https?://[^\s<>\"]+")


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def get_credentials() -> Credentials:
    creds: Credentials | None = None
    token_path = _project_root() / TOKEN_FILE
    creds_path = _project_root() / CREDENTIALS_FILE

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None  # fall through to interactive flow
        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), SCOPES)
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json())

    return creds


def get_calendar_service():
    return build("calendar", "v3", credentials=get_credentials())


def get_gmail_service():
    return build("gmail", "v1", credentials=get_credentials())


def _unwrap_google_redirect_url(u: str) -> str:
    """Calendar descriptions sometimes wrap meeting URLs as google.com/url?q=..."""
    if not u:
        return ""
    u = html.unescape(u.strip())
    parsed = urlparse(u)
    if parsed.netloc.endswith("google.com") and parsed.path == "/url":
        params = parse_qs(parsed.query)
        wrapped = params.get("q") or params.get("url")
        if wrapped and wrapped[0]:
            return unquote(wrapped[0])
    return u


def _normalize_meeting_url(u: str) -> str:
    u = _unwrap_google_redirect_url(u)
    return u.split("?")[0].rstrip("/").lower() if u else ""


def event_meeting_urls(event: dict) -> list[str]:
    """Pull candidate meeting URLs from native conference data, location, and description."""
    urls: list[str] = []

    for entry in event.get("conferenceData", {}).get("entryPoints", []):
        if entry.get("entryPointType") == "video" and entry.get("uri"):
            urls.append(entry["uri"])

    if event.get("hangoutLink"):
        urls.append(event["hangoutLink"])

    for field in ("location", "description"):
        value = event.get(field) or ""
        urls.extend(URL_RE.findall(value))

    cleaned: list[str] = []
    seen = set()
    for url in urls:
        url = url.rstrip(").,;]")
        url = _unwrap_google_redirect_url(url)
        normalized = _normalize_meeting_url(url)
        if normalized and normalized not in seen:
            cleaned.append(url)
            seen.add(normalized)
    return cleaned


def find_event_for_url(meeting_url: str, window_hours: int = 24) -> dict | None:
    """Find the Calendar event whose conference link matches `meeting_url`.

    Looks in the ±`window_hours` window around now (covers upcoming + recently started).
    Returns the raw event dict or None.
    """
    target = _normalize_meeting_url(meeting_url)
    if not target:
        return None
    service = get_calendar_service()
    now = dt.datetime.now(dt.timezone.utc)
    time_min = (now - dt.timedelta(hours=window_hours)).isoformat()
    time_max = (now + dt.timedelta(hours=window_hours)).isoformat()
    result = service.events().list(
        calendarId="primary",
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=50,
    ).execute()
    for event in result.get("items", []):
        for url in event_meeting_urls(event):
            if _normalize_meeting_url(url) == target:
                return event
    return None


def attendees_for_url(meeting_url: str) -> list[str]:
    """Return the email addresses of attendees on the calendar event matching `meeting_url`.

    Returns an empty list if no matching event, no attendees, or the call fails.
    """
    try:
        event = find_event_for_url(meeting_url)
    except Exception:
        return []
    if not event:
        return []
    recipients: list[str] = []
    seen = set()
    for attendee in event.get("attendees", []):
        email = attendee.get("email")
        if email and email not in seen:
            recipients.append(email)
            seen.add(email)

    organizer_email = event.get("organizer", {}).get("email")
    if organizer_email and organizer_email not in seen:
        recipients.append(organizer_email)

    return recipients
