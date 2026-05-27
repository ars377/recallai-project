"""Phase 1: list upcoming Google Calendar meetings, including their meeting URLs.

First run:
    - opens a browser for you to approve Calendar access
    - saves a token to token.json so future runs skip the login

Run from the project root:
    python scripts/list_meetings.py
"""
import datetime as dt
import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
CREDENTIALS_FILE = "credentials.json"
TOKEN_FILE = "token.json"


def get_calendar_service():
    """Authenticate (first time via browser, after that via cached token) and return a Calendar API client."""
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def extract_meeting_url(event):
    """Pull a Meet/Zoom/Teams URL out of an event, if present."""
    conf = event.get("conferenceData", {})
    for entry in conf.get("entryPoints", []):
        if entry.get("entryPointType") == "video":
            return entry.get("uri")
    return None


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
        meeting_url = extract_meeting_url(event)
        attendees = [a.get("email") for a in event.get("attendees", []) if a.get("email")]

        print(f"- {start}  {summary}")
        if meeting_url:
            print(f"    link: {meeting_url}")
        if attendees:
            print(f"    attendees: {', '.join(attendees)}")
        print()


if __name__ == "__main__":
    main()