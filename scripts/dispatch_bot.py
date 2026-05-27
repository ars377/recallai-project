"""Phase 6: end-to-end. Send a Recall.ai bot to a meeting, wait for it to finish,
download the transcript, summarize with Claude, save everything to a meeting folder.

Usage:
    python scripts/dispatch_bot.py https://meet.google.com/abc-defg-hij
    python scripts/dispatch_bot.py <meeting_url> --subject "Team Sync" --date 2026-05-27

While the bot is in the meeting this script will sit and poll. That's expected —
real meetings take minutes-to-hours and the script blocks until the bot leaves.
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src import recall_client
from src.file_org import make_meeting_folder, save_summary, save_transcript

load_dotenv()


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("meeting_url", help="Google Meet / Zoom / Teams URL the bot should join")
    p.add_argument("--subject", default="meeting", help='Meeting subject for folder name (default: "meeting")')
    p.add_argument("--date", help="Meeting date YYYY-MM-DD (default: today)")
    p.add_argument("--bot-name", default="RecallAI Notetaker", help="Display name the bot uses in the meeting")
    p.add_argument("--skip-summary", action="store_true", help="Save transcript only; skip the Claude summary step")
    return p.parse_args()


def main():
    args = parse_args()
    date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()
    folder = make_meeting_folder(args.subject, date)
    print(f"Meeting folder: {folder}\n")

    print(f"Dispatching bot to {args.meeting_url}...")
    bot = recall_client.create_bot(args.meeting_url, bot_name=args.bot_name)
    bot_id = bot["id"]
    print(f"  bot id: {bot_id}\n")

    print("Waiting for the bot to finish (this blocks until the meeting ends)...")
    bot = recall_client.wait_for_completion(bot_id)
    final_status = recall_client.current_status(bot)
    print(f"\nFinal status: {final_status}")

    if final_status in recall_client.TERMINAL_FAILURE:
        print("Bot did not complete successfully. Inspect the bot object for details.")
        print(bot)
        sys.exit(1)

    print("Downloading transcript...")
    turns = recall_client.download_transcript_json(bot)
    transcript_text = recall_client.format_transcript(turns)
    save_transcript(folder, transcript_text)
    print(f"  saved transcript.txt ({len(transcript_text)} chars)\n")

    if args.skip_summary:
        return

    from src.summarizer import summarize

    print("Summarizing with Claude...")
    summary = summarize(transcript_text)
    save_summary(folder, summary)
    print(f"  saved summary.txt\n")

    print("--- Summary ---\n")
    print(summary)


if __name__ == "__main__":
    main()
