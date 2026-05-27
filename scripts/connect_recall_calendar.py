"""Connect the locally-authorized Google Calendar account to Recall Calendar V2.

Run from the project root:
    conda run --no-capture-output -n recall python scripts/connect_recall_calendar.py

This creates a Recall calendar and stores its id in .recall_calendar.json.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.recall_calendar import create_google_calendar, load_calendar_state, retrieve_calendar, update_calendar

load_dotenv()


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--webhook-url",
        default=os.getenv("RECALL_CALENDAR_WEBHOOK_URL"),
        help="Optional public webhook URL for Recall calendar webhooks.",
    )
    p.add_argument(
        "--existing-id",
        default=os.getenv("RECALL_CALENDAR_ID"),
        help="Verify an existing Recall calendar id instead of creating a new one.",
    )
    p.add_argument(
        "--force-new",
        action="store_true",
        help="Create a new Recall calendar even if .recall_calendar.json already has one.",
    )
    return p.parse_args()


def main():
    args = parse_args()
    if args.existing_id:
        if args.webhook_url:
            calendar = update_calendar(args.existing_id, webhook_url=args.webhook_url)
            print(f"Updated Recall calendar webhook: {calendar.get('id')}")
        else:
            calendar = retrieve_calendar(args.existing_id)
            print(f"Recall calendar exists: {calendar.get('id')}")
        return

    existing_id = load_calendar_state().get("calendar_id")
    if existing_id and not args.force_new:
        if args.webhook_url:
            calendar = update_calendar(existing_id, webhook_url=args.webhook_url)
            print("Updated existing Recall Calendar V2 connection.")
            print(f"  calendar id: {calendar.get('id')}")
            print(f"  webhook url: {args.webhook_url}")
            return
        calendar = retrieve_calendar(existing_id)
        print(f"Using existing Recall calendar: {calendar.get('id')}")
        print("Pass --force-new to create another calendar connection.")
        return

    calendar = create_google_calendar(webhook_url=args.webhook_url)
    print("Created Recall Calendar V2 connection.")
    print(f"  calendar id: {calendar.get('id')}")
    print("  saved: .recall_calendar.json")


if __name__ == "__main__":
    main()
