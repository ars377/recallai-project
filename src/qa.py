"""Q&A over a meeting transcript using Claude.

The transcript is sent as a cacheable block so follow-up questions on the same meeting
reuse the cached prefix instead of re-tokenizing the full transcript every time.
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You answer questions about a meeting based strictly on the provided transcript.

Rules:
- Only use information from the transcript. If the answer isn't in the transcript, say "Not discussed in this meeting."
- Quote speakers when relevant: "Maya said …"
- Be concise — 1-3 sentences unless the question requires detail.
- Never invent attendees, dates, or numbers."""


def ask(transcript: str, question: str) -> str:
    """Ask a single question about the transcript. Returns the answer text."""
    client = Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": f"<transcript>\n{transcript}\n</transcript>",
                        "cache_control": {"type": "ephemeral"},
                    },
                    {
                        "type": "text",
                        "text": f"Question: {question}",
                    },
                ],
            }
        ],
    )
    return response.content[0].text


def load_qa(folder: Path) -> list[dict]:
    """Load Q&A history from qa.json. Returns empty list if file missing or corrupt."""
    path = folder / "qa.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def append_qa(folder: Path, question: str, answer: str) -> Path:
    """Append a Q&A pair to qa.json inside the meeting folder."""
    path = folder / "qa.json"
    history = load_qa(folder)
    history.append(
        {
            "timestamp": dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "question": question,
            "answer": answer,
        }
    )
    path.write_text(json.dumps(history, indent=2))
    return path
