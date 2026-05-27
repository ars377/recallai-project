"""Phase 2/3: summarize a transcript with Claude and save to a meeting folder.

By default the meeting folder name is derived from the transcript filename and today's date.
Pass --subject and --date to override (e.g. when running outside the Recall flow).

Run from the project root:
    python scripts/summarize_transcript.py samples/transcript_sample.txt
    python scripts/summarize_transcript.py samples/transcript_sample.txt --subject "Team Sync" --date 2026-05-27
"""
import argparse
import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.file_org import make_meeting_folder, save_summary, save_transcript
from src.summarizer import MODEL, summarize

load_dotenv()


def parse_args():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("transcript_path", help="Path to a transcript .txt file")
    p.add_argument("--subject", help="Meeting subject (defaults to transcript filename stem)")
    p.add_argument("--date", help="Meeting date in YYYY-MM-DD (defaults to today)")
    return p.parse_args()


def main():
    args = parse_args()
    transcript_path = Path(args.transcript_path)
    transcript = transcript_path.read_text()

    subject = args.subject or transcript_path.stem
    date = dt.date.fromisoformat(args.date) if args.date else dt.date.today()

    folder = make_meeting_folder(subject, date)
    print(f"Meeting folder: {folder}")

    save_transcript(folder, transcript)
    print(f"  saved transcript.txt")

    print(f"Summarizing with {MODEL}...")
    summary = summarize(transcript)
    save_summary(folder, summary)
    print(f"  saved summary.md\n")

    print("--- Summary ---\n")
    print(summary)


if __name__ == "__main__":
    main()
