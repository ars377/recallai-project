"""Q&A over a meeting transcript using Claude.

The transcript is sent as a cacheable block so follow-up questions on the same meeting
reuse the cached prefix instead of re-tokenizing the full transcript every time.
"""
from __future__ import annotations

import datetime as dt
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


def append_qa(folder: Path, question: str, answer: str) -> Path:
    """Append a Q&A pair to qa.md inside the meeting folder."""
    path = folder / "qa.md"
    timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    entry = f"## {timestamp}\n\n**Q:** {question}\n\n**A:** {answer}\n\n---\n\n"
    with path.open("a") as f:
        f.write(entry)
    return path
