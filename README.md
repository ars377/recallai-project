# RecallAI Project

Local meeting-notetaker app using Recall.ai, FastAPI, Claude, Google Calendar, and Gmail.

The app supports two flows:

- Manual: paste a Meet/Zoom/Teams URL and send a Recall.ai bot.
- Calendar V2: connect Google Calendar to Recall.ai, then schedule bots from calendar events.

## Setup

Create and fill your environment file:

```bash
cp .env.example .env
```

Required values:

```bash
RECALL_API_KEY=...
RECALL_REGION=us-west-2
ANTHROPIC_API_KEY=...
```

Install dependencies in your `recall` conda env:

```bash
conda activate recall
pip install -r requirements.txt
```

Add Google OAuth credentials:

1. Create a Google OAuth desktop client in Google Cloud.
2. Enable Google Calendar API.
3. Enable Gmail API if you want recap emails.
4. Download the OAuth client JSON.
5. Save it as `credentials.json` in the project root.

The first Google command will create `token.json`.

## Run The Web App

```bash
conda activate recall
python -m uvicorn app:app --reload
```

Open:

```text
http://localhost:8000
```

Meeting outputs are saved under:

```text
meetings/
```

## Manual Bot Flow

From the web app:

1. Open `http://localhost:8000`.
2. Paste the meeting join URL.
3. Enter a subject.
4. Submit.
5. Admit the bot in the meeting.

Or from the terminal:

```bash
conda run --no-capture-output -n recall python scripts/dispatch_bot.py "https://meet.google.com/abc-defg-hij" --subject "Team Sync"
```

## Google Calendar And Gmail Tests

List upcoming Calendar events and meeting links:

```bash
conda run --no-capture-output -n recall python scripts/list_meetings.py
```

Test Calendar attendee lookup:

```bash
conda run --no-capture-output -n recall python -c 'from src.google_auth import attendees_for_url; print(attendees_for_url("PASTE_MEETING_LINK_HERE"))'
```

If Google scopes change, delete `token.json` and authenticate again:

```bash
rm token.json
conda run --no-capture-output -n recall python scripts/list_meetings.py
```

## Calendar V2 Automatic Scheduling

Calendar V2 lets Recall.ai sync your Google Calendar so bots can be scheduled from calendar events without manually pasting meeting links.

### 1. Start The App

```bash
conda activate recall
python -m uvicorn app:app --reload
```

### 2. Expose Localhost

Using ngrok:

```bash
ngrok http 8000
```

Copy the public HTTPS URL, for example:

```text
https://abc123.ngrok-free.app
```

### 3. Connect Google Calendar To Recall

```bash
conda run --no-capture-output -n recall python scripts/connect_recall_calendar.py --webhook-url "https://abc123.ngrok-free.app/webhooks/recall/calendar"
```

This creates or updates `.recall_calendar.json` with the Recall calendar id.

### 4. Configure Recall Dashboard Webhook

In the Recall.ai dashboard, add a webhook endpoint:

```text
https://abc123.ngrok-free.app/webhooks/recall/calendar
```

Subscribe it to Calendar V2 events:

```text
calendar.sync_events
calendar.update
```

This dashboard/Svix endpoint is required for real automatic event notifications.

### 5. Test The Webhook Route

```bash
curl -X POST "https://abc123.ngrok-free.app/webhooks/recall/calendar" \
  -H "Content-Type: application/json" \
  -d '{"event":"test","data":{}}'
```

Then check:

```bash
tail -n 20 meetings/_webhooks.jsonl
```

You should see a `test` event.

### 6. Trigger Automatic Scheduling

Create or edit a Google Calendar event with a Meet/Zoom/Teams link.

Watch the FastAPI terminal for:

```text
[recall-calendar] webhook calendar.sync_events
[recall-calendar] scheduled ...
```

The bot should join at the event time. If the event is already happening, the app schedules it to join now.

After the bot finishes, the app downloads the transcript, summarizes it, and shows the meeting on the Meetings page.

## Calendar V2 Manual Scheduling Fallback

Preview schedulable events:

```bash
conda run --no-capture-output -n recall python scripts/schedule_calendar_bots.py --days 7 --dry-run
```

Schedule them:

```bash
conda run --no-capture-output -n recall python scripts/schedule_calendar_bots.py --days 7
```

Include meetings that started recently:

```bash
conda run --no-capture-output -n recall python scripts/schedule_calendar_bots.py --days 7 --lookback-minutes 360
```

Debug raw Recall Calendar V2 event JSON:

```bash
conda run --no-capture-output -n recall python scripts/schedule_calendar_bots.py --days 7 --debug-json
```

## Useful Checks

Recall API smoke test:

```bash
conda run --no-capture-output -n recall python scripts/smoke_test_recall.py
```

Verify Recall Calendar V2 connection:

```bash
conda run --no-capture-output -n recall python -c 'from src.recall_calendar import default_calendar_id, retrieve_calendar; import json; print(json.dumps(retrieve_calendar(default_calendar_id()), indent=2))'
```

Syntax check:

```bash
conda run --no-capture-output -n recall python -m py_compile app.py src/*.py scripts/*.py
```

## Common Issues

`Gmail API has not been used...`

Enable Gmail API in Google Cloud for the project used by `credentials.json`, wait a few minutes, then retry.

No `calendar.sync_events` in `meetings/_webhooks.jsonl`

Your app received no real Calendar V2 webhook. Confirm:

- FastAPI is running.
- ngrok/cloudflared is running.
- Recall dashboard webhook endpoint is configured.
- Endpoint subscribes to `calendar.sync_events`.
- You edited or created the calendar event after configuring the endpoint.

Bot joined but meeting is not on the Meetings page

The bot may have been scheduled before the local Calendar V2 post-processing code was running. Trigger a fresh calendar update/new event with FastAPI running.

Bot does not join

Confirm the Calendar V2 dry-run sees the event:

```bash
conda run --no-capture-output -n recall python scripts/schedule_calendar_bots.py --days 7 --dry-run
```

If the meeting already ended, create a new event with a fresh Meet link.

## Local Files

Ignored local files:

```text
.env
credentials.json
token.json
.recall_calendar.json
meetings/
```
