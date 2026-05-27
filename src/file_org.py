"""Folder + file organization for meeting outputs.

Folder layout produced by `make_meeting_folder`:
    meetings/<subject>_<YYYY-MM-DD>/
        transcript.txt
        summary.md
        qa.md         (added later by the Q&A flow)

Subject and date can be supplied manually, OR pulled from a Google Calendar event
via `from_calendar_event` so the Recall pipeline can run hands-free.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path

MEETINGS_ROOT = Path("meetings")


def sanitize_subject(subject: str) -> str:
    """Convert a meeting title into a safe folder-name fragment.

    'Team Sync — Q3 Planning' -> 'team-sync-q3-planning'
    """
    s = subject.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "untitled"


def make_meeting_folder(subject: str, date: dt.date | dt.datetime | None = None) -> Path:
    """Create (if missing) and return the folder for a meeting.

    Args:
        subject: human-readable meeting title (will be sanitized).
        date:    date of the meeting. Defaults to today (local time).
    """
    if date is None:
        date = dt.date.today()
    elif isinstance(date, dt.datetime):
        date = date.date()

    folder_name = f"{sanitize_subject(subject)}_{date.isoformat()}"
    folder = MEETINGS_ROOT / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def from_calendar_event(event: dict) -> Path:
    """Make a meeting folder from a Google Calendar event dict (as returned by the Calendar API)."""
    subject = event.get("summary", "untitled")
    start = event["start"].get("dateTime") or event["start"].get("date")
    date = dt.datetime.fromisoformat(start.replace("Z", "+00:00")) if "T" in start else dt.date.fromisoformat(start)
    return make_meeting_folder(subject, date)


def save_transcript(folder: Path, text: str) -> Path:
    path = folder / "transcript.txt"
    path.write_text(text)
    return path


def save_summary(folder: Path, markdown: str) -> Path:
    path = folder / "summary.md"
    path.write_text(markdown)
    return path
