"""Meeting transcript summarization via Claude."""
from __future__ import annotations

from anthropic import Anthropic

MODEL = "claude-sonnet-4-6"

SYSTEM_PROMPT = """You summarize meeting transcripts. Produce concise, scannable markdown with these sections:

## Overview
1-2 sentences capturing what the meeting was about.

## Key Decisions
- Bullet list of decisions made. Skip if none.

## Action Items
- Bullet list. Each item: **owner** — task — deadline (if mentioned)

## Open Questions
- Bullet list of things left unresolved. Skip if none.

Be faithful to the transcript. Don't invent details. If a section is empty, omit it entirely."""


def summarize(transcript: str) -> str:
    client = Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": f"Here is the meeting transcript:\n\n{transcript}"}
        ],
    )
    return response.content[0].text
