"""Schedule Recall bots for upcoming Recall Calendar V2 events.

Run from the project root:
    conda run --no-capture-output -n recall python scripts/schedule_calendar_bots.py --days 7

Use --dry-run first to see what would be scheduled.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.recall_calendar import (
    default_calendar_id,
    event_end_time,
    event_has_meeting_url,
    event_start_time,
    event_title,
    list_calendar_events,
    parse_event_time,
    schedule_bot_for_event,
)

load_dotenv()


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--calendar-id", help="Recall Calendar V2 id. Defaults to .recall_calendar.json or RECALL_CALENDAR_ID.")
    p.add_argument("--days", type=int, default=7, help="How many future days to scan. Default: 7.")
    p.add_argument("--lookback-minutes", type=int, default=180, help="Include meetings that started recently. Default: 180.")
    p.add_argument("--include-no-link", action="store_true", help="Try scheduling events even if no meeting URL is visible.")
    p.add_argument("--dry-run", action="store_true", help="List events without calling the schedule endpoint.")
    p.add_argument("--bot-name", default="RecallAI Notetaker", help="Display name for scheduled bots.")
    p.add_argument("--dedupe-prefix", default="recallai-project", help="Prefix for Recall bot deduplication keys.")
    p.add_argument("--debug-json", action="store_true", help="Print the raw returned events and exit.")
    return p.parse_args()


def main():
    args = parse_args()
    calendar_id = args.calendar_id or default_calendar_id()
    now = dt.datetime.now(dt.timezone.utc)
    start = now - dt.timedelta(minutes=args.lookback_minutes)
    end = now + dt.timedelta(days=args.days)

    events = list_calendar_events(calendar_id, start_gte=start, start_lte=end)
    if not events:
        print("No upcoming Recall calendar events found.")
        return
    if args.debug_json:
        print(json.dumps(events, indent=2))
        return

    scheduled = 0
    skipped = 0
    failed = 0
    for event in events:
        if event.get("is_deleted"):
            skipped += 1
            continue

        title = event_title(event)
        start_label = event_start_time(event) or "(no start)"
        starts_at = parse_event_time(event_start_time(event))
        ends_at = parse_event_time(event_end_time(event))
        join_at = None
        if ends_at and ends_at < now:
            skipped += 1
            print(f"skip   {start_label}  {title}  ({event.get('id')}) already ended")
            continue
        if starts_at and starts_at < now:
            join_at = now.isoformat().replace("+00:00", "Z")
        has_link = event_has_meeting_url(event)
        event_id = event.get("id")

        if not has_link and not args.include_no_link:
            skipped += 1
            print(f"skip   {start_label}  {title}  ({event_id}) no visible meeting URL")
            continue

        if args.dry_run:
            suffix = " join now" if join_at else ""
            print(f"would  {start_label}  {title}  ({event_id}){suffix}")
            scheduled += 1
            continue

        try:
            schedule_bot_for_event(
                event,
                deduplication_namespace=args.dedupe_prefix,
                bot_name=args.bot_name,
                join_at=join_at,
            )
        except Exception as exc:
            failed += 1
            print(f"fail   {start_label}  {title}  ({event_id}) {type(exc).__name__}: {exc}")
            continue

        scheduled += 1
        print(f"ok     {start_label}  {title}  ({event_id})")

    label = "would schedule" if args.dry_run else "scheduled"
    print(f"\n{label}: {scheduled}  skipped: {skipped}  failed: {failed}")


if __name__ == "__main__":
    main()
