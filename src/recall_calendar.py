"""Recall.ai Calendar V2 helpers.

Calendar V2 is the "connected calendar" flow: Recall syncs the user's calendar,
then bots are scheduled against Recall calendar-event IDs instead of raw meeting
URLs.
"""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import requests
from dotenv import load_dotenv

from src.google_auth import CREDENTIALS_FILE, get_credentials

load_dotenv()

STATE_FILE = Path(".recall_calendar.json")


def _api_key() -> str:
    return os.environ["RECALL_API_KEY"]


def _base_url() -> str:
    region = os.getenv("RECALL_REGION", "us-west-2")
    return f"https://{region}.recall.ai/api/v2"


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Token {_api_key()}",
        "Content-Type": "application/json",
    }


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _google_oauth_client() -> tuple[str, str]:
    path = _project_root() / CREDENTIALS_FILE
    data = json.loads(path.read_text())
    client = data.get("installed") or data.get("web") or {}
    client_id = client.get("client_id")
    client_secret = client.get("client_secret")
    if not client_id or not client_secret:
        raise RuntimeError(f"{CREDENTIALS_FILE} is missing client_id/client_secret")
    return client_id, client_secret


def google_refresh_token() -> str:
    creds = get_credentials()
    if not creds.refresh_token:
        raise RuntimeError(
            "Google token has no refresh_token. Delete token.json, rerun auth, "
            "and make sure the OAuth flow requests offline access."
        )
    return creds.refresh_token


def create_google_calendar(webhook_url: str | None = None) -> dict[str, Any]:
    """Create a Recall Calendar V2 calendar for the locally authorized Google account."""
    client_id, client_secret = _google_oauth_client()
    payload: dict[str, Any] = {
        "platform": "google_calendar",
        "oauth_client_id": client_id,
        "oauth_client_secret": client_secret,
        "oauth_refresh_token": google_refresh_token(),
    }
    if webhook_url:
        payload["webhook_url"] = webhook_url

    response = requests.post(f"{_base_url()}/calendars/", headers=_headers(), json=payload, timeout=30)
    response.raise_for_status()
    calendar = response.json()
    save_calendar_state(calendar)
    return calendar


def retrieve_calendar(calendar_id: str) -> dict[str, Any]:
    response = requests.get(f"{_base_url()}/calendars/{calendar_id}/", headers=_headers(), timeout=30)
    response.raise_for_status()
    return response.json()


def update_calendar(calendar_id: str, **fields) -> dict[str, Any]:
    payload = {key: value for key, value in fields.items() if value is not None}
    response = requests.patch(f"{_base_url()}/calendars/{calendar_id}/", headers=_headers(), json=payload, timeout=30)
    response.raise_for_status()
    calendar = response.json()
    save_calendar_state(calendar)
    return calendar


def load_calendar_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text())


def save_calendar_state(calendar: dict[str, Any]) -> None:
    state = {
        "calendar_id": calendar.get("id"),
        "platform": calendar.get("platform", "google_calendar"),
        "created_at": dt.datetime.now().isoformat(timespec="seconds"),
    }
    STATE_FILE.write_text(json.dumps(state, indent=2))


def default_calendar_id() -> str:
    calendar_id = os.getenv("RECALL_CALENDAR_ID") or load_calendar_state().get("calendar_id")
    if not calendar_id:
        raise RuntimeError(
            "No Recall calendar id found. Run scripts/connect_recall_calendar.py first "
            "or set RECALL_CALENDAR_ID in .env."
        )
    return calendar_id


def _iso_utc(value: dt.datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=dt.timezone.utc)
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def _next_cursor(value: str | None) -> str | None:
    if not value:
        return None
    if value.startswith("http"):
        parsed = urlparse(value)
        return parse_qs(parsed.query).get("cursor", [None])[0]
    return value


def list_calendar_events(
    calendar_id: str,
    start_gte: dt.datetime | None = None,
    start_lte: dt.datetime | None = None,
    updated_gte: dt.datetime | None = None,
) -> list[dict[str, Any]]:
    """List Recall Calendar V2 events, following cursor pagination when present."""
    params: dict[str, str] = {"calendar_id": calendar_id}
    if start_gte:
        params["start_time__gte"] = _iso_utc(start_gte)
    if start_lte:
        params["start_time__lte"] = _iso_utc(start_lte)
    if updated_gte:
        params["updated_at__gte"] = _iso_utc(updated_gte)

    events: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        page_params = dict(params)
        if cursor:
            page_params["cursor"] = cursor
        response = requests.get(f"{_base_url()}/calendar-events/", headers=_headers(), params=page_params, timeout=30)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, list):
            events.extend(data)
            return events
        events.extend(data.get("results", []))
        cursor = _next_cursor(data.get("next"))
        if not cursor:
            return events


def event_title(event: dict[str, Any]) -> str:
    raw = event.get("raw") if isinstance(event.get("raw"), dict) else {}
    return (
        event.get("summary")
        or event.get("title")
        or event.get("subject")
        or event.get("name")
        or raw.get("summary")
        or raw.get("title")
        or raw.get("subject")
        or "(untitled)"
    )


def event_start_time(event: dict[str, Any]) -> str | None:
    start = event.get("start_time") or event.get("start")
    if isinstance(start, dict):
        return start.get("dateTime") or start.get("date")
    return start


def event_end_time(event: dict[str, Any]) -> str | None:
    end = event.get("end_time") or event.get("end")
    if isinstance(end, dict):
        return end.get("dateTime") or end.get("date")
    return end


def parse_event_time(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def event_attendees(event: dict[str, Any]) -> list[str]:
    """Extract attendee/organizer emails from a Recall Calendar V2 event."""
    raw = event.get("raw") if isinstance(event.get("raw"), dict) else {}
    recipients: list[str] = []
    seen = set()
    for attendee in raw.get("attendees", []) or []:
        email = attendee.get("email")
        if email and email not in seen:
            recipients.append(email)
            seen.add(email)
    organizer_email = (raw.get("organizer") or {}).get("email")
    if organizer_email and organizer_email not in seen:
        recipients.append(organizer_email)
    return recipients


def event_has_meeting_url(event: dict[str, Any]) -> bool:
    """Best-effort check before scheduling; the schedule endpoint remains authoritative."""
    stack: list[Any] = [event]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for value in item.values():
                if isinstance(value, str) and (
                    "meet.google.com/" in value
                    or "zoom.us/" in value
                    or "teams.microsoft.com/" in value
                    or "webex.com/" in value
                    or "gotomeeting.com/" in value
                ):
                    return True
                if isinstance(value, (dict, list)):
                    stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
    return False


def bot_config_for_event(
    event: dict[str, Any],
    bot_name: str = "RecallAI Notetaker",
    join_at: str | None = None,
) -> dict[str, Any]:
    config: dict[str, Any] = {
        "bot_name": bot_name,
        "recording_config": {
            "transcript": {
                "provider": {
                    "recallai_streaming": {
                        "mode": "prioritize_accuracy",
                        "language_code": "auto",
                    }
                }
            }
        },
    }
    start = join_at or event_start_time(event)
    if start:
        config["join_at"] = start
    return config


def schedule_bot_for_event(
    event: dict[str, Any],
    deduplication_namespace: str = "recallai-project",
    bot_name: str = "RecallAI Notetaker",
    join_at: str | None = None,
) -> dict[str, Any]:
    event_id = event["id"]
    deduplication_key = event.get("ical_uid") or event.get("icalUid") or event_id
    payload = {
        "deduplication_key": f"{deduplication_namespace}:{deduplication_key}",
        "bot_config": bot_config_for_event(event, bot_name=bot_name, join_at=join_at),
    }
    response = requests.post(
        f"{_base_url()}/calendar-events/{event_id}/bot/",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def scheduled_bot_refs(event: dict[str, Any]) -> list[dict[str, Any]]:
    return event.get("bots") or []
