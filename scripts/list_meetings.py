"""Phase 1: list upcoming Google Calendar meetings, including their meeting URLs.

First run:
    - opens a browser for you to approve Calendar + Gmail access
    - saves a token to token.json so future runs skip the login

Run from the project root:
    python scripts/list_meetings.py
"""
import datetime as dt
import sys
from pathlib import Path

# Allow `from src...` imports when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.google_auth import event_meeting_urls, get_calendar_service


def main():
    service = get_calendar_service()

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    print("Fetching the next 10 upcoming events...\n")

    result = service.events().list(
        calendarId="primary",
        timeMin=now,
        maxResults=10,
        singleEvents=True,
        orderBy="startTime",
    ).execute()

    events = result.get("items", [])
    if not events:
        print("No upcoming events found.")
        return

    for event in events:
        start = event["start"].get("dateTime", event["start"].get("date"))
        summary = event.get("summary", "(no title)")
        meeting_urls = event_meeting_urls(event)
        attendees = [a.get("email") for a in event.get("attendees", []) if a.get("email")]

        print(f"- {start}  {summary}")
        for meeting_url in meeting_urls:
            print(f"    link: {meeting_url}")
        if attendees:
            print(f"    attendees: {', '.join(attendees)}")
        print()


if __name__ == "__main__":
    main()
