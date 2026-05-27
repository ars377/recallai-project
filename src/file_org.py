"""Folder + file organization for meeting outputs.

Hierarchy:
    meetings/                              ← MEETINGS_ROOT
    ├── <top-level-meeting>_<date>/        ← a meeting at the root
    │     ├── .meeting                     ← marker file
    │     ├── transcript.txt
    │     └── summary.txt
    ├── work/                              ← a user-created folder
    │   └── clients/                       ← arbitrarily nested
    │       └── <meeting>_<date>/
    │           ├── .meeting
    │           ├── transcript.txt
    │           └── summary.txt
    └── _jobs.json                         ← job log (ignored by browse)

A "meeting" is a directory containing `.meeting`, `transcript.txt`, or `summary.txt`.
Anything else under MEETINGS_ROOT is treated as a regular folder.
"""
from __future__ import annotations

import datetime as dt
import re
import shutil
from pathlib import Path

MEETINGS_ROOT = Path("meetings")
MEETING_MARKER = ".meeting"
_MEETING_FILES = {MEETING_MARKER, "transcript.txt", "summary.txt"}


# ---------- sanitization ----------

def sanitize_subject(subject: str) -> str:
    """Convert a meeting title into a safe, predictable kebab-case folder fragment.

    Used to build the auto-generated `<subject>_<date>` meeting folder name.
    'Team Sync — Q3 Planning' -> 'team-sync-q3-planning'
    """
    s = subject.strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    return s or "untitled"


def sanitize_name(name: str) -> str:
    """Validate a user-typed folder or meeting name without kebab-casing it.

    Raises ValueError for empty/dangerous input. Preserves capitalization,
    spaces, underscores, hyphens, and digits so renames feel natural.
    """
    s = (name or "").strip()
    if not s:
        raise ValueError("Name is required")
    if "/" in s or "\\" in s:
        raise ValueError("Name cannot contain '/' or '\\'")
    if s in (".", ".."):
        raise ValueError("Invalid name")
    if s.startswith("."):
        raise ValueError("Name cannot start with '.'")
    return s


# ---------- path safety ----------

def resolve_under_meetings(rel_path: str) -> Path:
    """Resolve a user-supplied relative path under MEETINGS_ROOT.

    Raises ValueError if the path tries to escape (absolute path, `..`, etc.).
    Accepts the empty string to mean MEETINGS_ROOT itself.
    """
    rel_path = (rel_path or "").strip().strip("/")
    if rel_path == "":
        MEETINGS_ROOT.mkdir(parents=True, exist_ok=True)
        return MEETINGS_ROOT.resolve()
    parts = Path(rel_path).parts
    if any(p in ("..", "") for p in parts) or any(p.startswith(".") for p in parts if p != MEETING_MARKER):
        raise ValueError(f"Invalid path: {rel_path!r}")
    MEETINGS_ROOT.mkdir(parents=True, exist_ok=True)
    candidate = (MEETINGS_ROOT / rel_path).resolve()
    root = MEETINGS_ROOT.resolve()
    if not (candidate == root or root in candidate.parents):
        raise ValueError(f"Path escapes meetings root: {rel_path!r}")
    return candidate


# ---------- meeting vs folder detection ----------

def is_meeting_folder(path: Path) -> bool:
    """True if `path` is a directory we should treat as a meeting (not a user folder)."""
    if not path.is_dir():
        return False
    try:
        names = {p.name for p in path.iterdir()}
    except OSError:
        return False
    return bool(names & _MEETING_FILES)


# ---------- browse helpers ----------

def list_browse_entries(rel_path: str = "") -> tuple[list[str], list[str]]:
    """Return (folders, meetings) at the given path under MEETINGS_ROOT.

    Names only — sorted, with hidden / underscore-prefixed entries (like _jobs.json) skipped.
    """
    target = resolve_under_meetings(rel_path)
    folders: list[str] = []
    meetings: list[str] = []
    if not target.exists():
        return folders, meetings
    for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        if child.name.startswith("_") or child.name.startswith("."):
            continue
        if is_meeting_folder(child):
            meetings.append(child.name)
        else:
            folders.append(child.name)
    return folders, meetings


def list_all_folders() -> list[str]:
    """Recursively list every non-meeting folder, returned as slash-separated relative paths.

    Used to populate the dispatch form's parent_path dropdown.
    """
    root = resolve_under_meetings("")
    out: list[str] = []

    def walk(current: Path, prefix: str) -> None:
        try:
            children = sorted(current.iterdir(), key=lambda p: p.name.lower())
        except OSError:
            return
        for child in children:
            if not child.is_dir():
                continue
            if child.name.startswith("_") or child.name.startswith("."):
                continue
            if is_meeting_folder(child):
                continue
            rel = f"{prefix}/{child.name}" if prefix else child.name
            out.append(rel)
            walk(child, rel)

    walk(root, "")
    return out


def count_meetings_in(rel_path: str) -> int:
    """Count meetings nested anywhere under rel_path (used for the folder badge)."""
    target = resolve_under_meetings(rel_path)
    if not target.exists():
        return 0
    n = 0
    for path in target.rglob("*"):
        if path.is_dir() and is_meeting_folder(path):
            n += 1
    return n


# ---------- mutators ----------

def create_folder(parent_rel_path: str, name: str) -> Path:
    """Create a new (non-meeting) folder under `parent_rel_path`.

    The name is validated (path-safe, non-empty) but not kebab-cased — what the user
    types is what they get. Idempotent: if it already exists, returns it unchanged.
    """
    safe_name = sanitize_name(name)
    parent = resolve_under_meetings(parent_rel_path)
    parent.mkdir(parents=True, exist_ok=True)
    folder = parent / safe_name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def make_meeting_folder(
    subject: str,
    date: dt.date | dt.datetime | None = None,
    parent_rel_path: str = "",
) -> Path:
    """Create (if missing) and return the folder for a meeting.

    The returned folder gets a `.meeting` marker so browse code can distinguish it
    from user-created folders.
    """
    if date is None:
        date = dt.date.today()
    elif isinstance(date, dt.datetime):
        date = date.date()

    parent = resolve_under_meetings(parent_rel_path)
    folder_name = f"{sanitize_subject(subject)}_{date.isoformat()}"
    folder = parent / folder_name
    folder.mkdir(parents=True, exist_ok=True)
    (folder / MEETING_MARKER).touch(exist_ok=True)
    return folder


def from_calendar_event(event: dict, parent_rel_path: str = "") -> Path:
    """Make a meeting folder from a Google Calendar event dict."""
    subject = event.get("summary", "untitled")
    start = event["start"].get("dateTime") or event["start"].get("date")
    if "T" in start:
        date = dt.datetime.fromisoformat(start.replace("Z", "+00:00"))
    else:
        date = dt.date.fromisoformat(start)
    return make_meeting_folder(subject, date, parent_rel_path)


# ---------- writers ----------

def save_transcript(folder: Path, text: str) -> Path:
    path = folder / "transcript.txt"
    path.write_text(text)
    return path


def save_summary(folder: Path, markdown: str) -> Path:
    path = folder / "summary.txt"
    path.write_text(markdown)
    return path


# ---------- in-app reorganization ----------

def rename_entry(rel_path: str, new_name: str) -> Path:
    """Rename a folder or meeting in place. Returns the new path."""
    source = resolve_under_meetings(rel_path)
    if source == MEETINGS_ROOT.resolve():
        raise ValueError("Cannot rename the meetings root")
    safe_name = sanitize_name(new_name)
    dest = source.parent / safe_name
    if dest == source:
        return source
    if dest.exists():
        raise ValueError(f"A folder/meeting named {safe_name!r} already exists here")
    source.rename(dest)
    return dest


def delete_entry(rel_path: str) -> None:
    """Recursively delete a folder or meeting. Destructive."""
    target = resolve_under_meetings(rel_path)
    if target == MEETINGS_ROOT.resolve():
        raise ValueError("Cannot delete the meetings root")
    if not target.is_dir():
        raise ValueError("Not a directory")
    shutil.rmtree(target)


def move_entry(source_rel_path: str, dest_parent_rel_path: str) -> Path:
    """Move a folder or meeting into `dest_parent_rel_path`. Returns the new path."""
    source = resolve_under_meetings(source_rel_path)
    dest_parent = resolve_under_meetings(dest_parent_rel_path)
    if source == MEETINGS_ROOT.resolve():
        raise ValueError("Cannot move the meetings root")
    if not source.is_dir():
        raise ValueError("Source is not a directory")
    if not dest_parent.is_dir():
        raise ValueError("Destination parent is not a directory")
    if is_meeting_folder(dest_parent):
        raise ValueError("Cannot move an entry inside a meeting folder")
    if dest_parent == source or source in dest_parent.parents:
        raise ValueError("Cannot move a folder into itself or one of its descendants")
    if dest_parent == source.parent:
        return source  # already there — no-op
    dest = dest_parent / source.name
    if dest.exists():
        raise ValueError(f"Destination already has an entry named {source.name!r}")
    shutil.move(str(source), str(dest))
    return dest
