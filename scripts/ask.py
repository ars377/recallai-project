"""Phase 4: ask questions about a previously-summarized meeting.

Usage (one-shot):
    python scripts/ask.py meetings/team-sync_2026-05-27 "When are we launching?"

Usage (interactive REPL — keep asking questions in a loop):
    python scripts/ask.py meetings/team-sync_2026-05-27
"""
import sys
from pathlib import Path

# Allow `from src...` imports when running this file directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from src.qa import append_qa, ask

load_dotenv()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    folder = Path(sys.argv[1])
    transcript_path = folder / "transcript.txt"
    if not transcript_path.exists():
        print(f"ERROR: no transcript.txt found in {folder}")
        sys.exit(1)
    transcript = transcript_path.read_text()

    if len(sys.argv) >= 3:
        question = " ".join(sys.argv[2:])
        answer = ask(transcript, question)
        print(answer)
        append_qa(folder, question, answer)
        return

    # Interactive mode
    print(f"Q&A on {folder}. Type a question (or Ctrl-D / blank line to quit).\n")
    while True:
        try:
            question = input("Q: ").strip()
        except EOFError:
            print()
            break
        if not question:
            break
        answer = ask(transcript, question)
        print(f"\nA: {answer}\n")
        append_qa(folder, question, answer)


if __name__ == "__main__":
    main()
