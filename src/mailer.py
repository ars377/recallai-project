"""Send meeting recap emails via the Gmail API."""
from __future__ import annotations

import base64
import mimetypes
from email.message import EmailMessage
from pathlib import Path

from src.google_auth import get_gmail_service


def default_email_body(meeting_name: str, summary: str | None) -> str:
    """The body the UI pre-fills, also used for auto-send."""
    parts = [
        "Hi all,",
        "",
        f"Attached are the notes from {meeting_name}.",
        "",
    ]
    if summary:
        parts.extend(["Summary:", "", summary.strip()])
    else:
        parts.append("(Summary not yet available — see the attached transcript.)")
    return "\n".join(parts)


def default_email_subject(meeting_name: str) -> str:
    return f"Meeting notes: {meeting_name}"


def send_meeting_email(
    to: list[str],
    subject: str,
    body: str,
    attachments: list[Path] | None = None,
) -> dict:
    """Send an email via Gmail. Returns the API response (id + threadId)."""
    if not to:
        raise ValueError("At least one recipient is required")

    msg = EmailMessage()
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body)

    for path in attachments or []:
        data = path.read_bytes()
        ctype, _ = mimetypes.guess_type(path.name)
        if ctype is None:
            maintype, subtype = "application", "octet-stream"
        else:
            maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=path.name)

    encoded = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    service = get_gmail_service()
    return service.users().messages().send(userId="me", body={"raw": encoded}).execute()
