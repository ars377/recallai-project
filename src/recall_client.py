"""Thin wrapper around the Recall.ai REST API for dispatching meeting bots.

Note: Recall recommends webhooks over polling for production. We poll here because
it's simpler for a local dev script. Swap to webhooks later if you self-host.
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests
from dotenv import load_dotenv

load_dotenv()


def _config() -> tuple[str, str]:
    api_key = os.environ["RECALL_API_KEY"]
    region = os.getenv("RECALL_REGION", "us-west-2")
    base_url = f"https://{region}.recall.ai/api/v1"
    return api_key, base_url


def _headers() -> dict[str, str]:
    api_key, _ = _config()
    return {
        "Authorization": f"Token {api_key}",
        "Content-Type": "application/json",
    }


# Status codes Recall.ai uses. Bot is finished when status reaches one of TERMINAL_STATUSES.
TERMINAL_SUCCESS = {"done"}
TERMINAL_FAILURE = {"fatal", "media_expired"}
TERMINAL_STATUSES = TERMINAL_SUCCESS | TERMINAL_FAILURE


def create_bot(meeting_url: str, bot_name: str = "RecallAI Notetaker") -> dict[str, Any]:
    """Dispatch a bot to join `meeting_url` and produce a transcript via recallai_streaming."""
    _, base_url = _config()
    payload = {
        "meeting_url": meeting_url,
        "bot_name": bot_name,
        "recording_config": {
            "transcript": {
                "provider": {
                    "recallai_streaming": {
                        "mode": "prioritize_accuracy",
                        "language_code": "auto",
                    }
                }
            }
        },
    }
    r = requests.post(f"{base_url}/bot/", headers=_headers(), json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def get_bot(bot_id: str) -> dict[str, Any]:
    """Fetch the current state of a bot."""
    _, base_url = _config()
    r = requests.get(f"{base_url}/bot/{bot_id}/", headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()


def current_status(bot: dict[str, Any]) -> str:
    """Extract the latest status code from a bot object."""
    changes = bot.get("status_changes") or []
    if changes:
        return changes[-1].get("code", "unknown")
    return bot.get("status", {}).get("code", "unknown")


def wait_for_completion(
    bot_id: str,
    poll_interval: float = 20.0,
    timeout: float = 4 * 3600,
) -> dict[str, Any]:
    """Poll the bot until it reaches a terminal status (done/fatal/media_expired)."""
    deadline = time.time() + timeout
    last_status: str | None = None
    while True:
        bot = get_bot(bot_id)
        status = current_status(bot)
        if status != last_status:
            print(f"[recall] bot {bot_id[:8]}… status: {status}")
            last_status = status
        if status in TERMINAL_STATUSES:
            return bot
        if time.time() > deadline:
            raise TimeoutError(f"Bot {bot_id} did not finish within {timeout}s (last status: {status})")
        time.sleep(poll_interval)


def transcript_download_url(bot: dict[str, Any]) -> str | None:
    """Pull the transcript download URL out of a finished bot's recording artifacts."""
    recordings = bot.get("recordings") or []
    for recording in recordings:
        url = (
            recording.get("media_shortcuts", {})
            .get("transcript", {})
            .get("data", {})
            .get("download_url")
        )
        if url:
            return url
    return None


def download_transcript_json(bot: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetch the raw transcript JSON from Recall's CDN."""
    url = transcript_download_url(bot)
    if not url:
        raise RuntimeError("Bot has no transcript download URL — recording may not include a transcript.")
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.json()


def format_transcript(turns: list[dict[str, Any]]) -> str:
    """Convert Recall's transcript JSON into 'Speaker N: text' plain-text lines."""
    lines: list[str] = []
    for turn in turns:
        speaker = turn.get("participant", {}).get("name") or f"Speaker {turn.get('participant', {}).get('id', '?')}"
        words = turn.get("words") or []
        text = " ".join(w.get("text", "") for w in words).strip()
        if not text:
            text = turn.get("text", "").strip()
        if text:
            lines.append(f"{speaker}: {text}")
    return "\n\n".join(lines)
