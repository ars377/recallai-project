"""Local FastAPI web app for RecallAI.

Run with:
    conda run --no-capture-output -n recall python -m uvicorn app:app --reload

Then open http://localhost:8000
"""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import threading
import uuid
import zipfile
from pathlib import Path
from urllib.parse import quote, urlencode

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.file_org import (
    MEETINGS_ROOT,
    count_meetings_in,
    create_folder,
    delete_entry,
    is_meeting_folder,
    list_all_folders,
    list_browse_entries,
    make_meeting_folder,
    move_entry,
    rename_entry,
    resolve_under_meetings,
    save_summary,
    save_transcript,
)
from src.google_auth import attendees_for_url
from src.mailer import default_email_body, default_email_subject, send_meeting_email
from src.recall_client import (
    TERMINAL_FAILURE,
    create_bot,
    current_status,
    download_transcript_json,
    format_transcript,
    wait_for_completion,
)
from src.qa import append_qa, ask, load_qa
from src.summarizer import summarize

load_dotenv()

app = FastAPI(title="RecallAI")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

JOBS_FILE = MEETINGS_ROOT / "_jobs.json"
ALLOWED_DOWNLOADS = {"transcript.txt", "summary.txt"}
MEETING_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_JOBS_LOCK = threading.RLock()
_TERMINAL_JOB_STATUSES = {"completed", "failed"}


# ---------- url helpers ----------

def path_url(rel_path: str) -> str:
    """Quote a slash-separated meetings path for use inside a URL path."""
    return quote((rel_path or "").strip("/"), safe="/")


def browse_url(path: str = "") -> str:
    path = (path or "").strip("/")
    return f"/browse?{urlencode({'path': path})}" if path else "/browse"


def meeting_url(path: str, **params) -> str:
    base = f"/meetings/{path_url(path)}"
    clean_params = {k: v for k, v in params.items() if v not in (None, "", 0)}
    return f"{base}?{urlencode(clean_params)}" if clean_params else base


templates.env.filters["path_url"] = path_url
templates.env.filters["query_value"] = lambda value: urlencode({"_": value or ""})[2:]


# ---------- job log ----------

def _jobs_load_unlocked() -> list[dict]:
    if not JOBS_FILE.exists():
        return []
    try:
        jobs = json.loads(JOBS_FILE.read_text())
    except json.JSONDecodeError:
        return []
    # Legacy entries stored folder as "meetings/<name>". New routes expect a path
    # relative to MEETINGS_ROOT, so strip the leading "meetings/" if present.
    for j in jobs:
        f = j.get("folder")
        if isinstance(f, str) and f.startswith("meetings/"):
            j["folder"] = f[len("meetings/"):]
    return jobs


def _jobs_save_unlocked(jobs: list[dict]) -> None:
    MEETINGS_ROOT.mkdir(parents=True, exist_ok=True)
    tmp = JOBS_FILE.with_name(f"{JOBS_FILE.name}.tmp")
    tmp.write_text(json.dumps(jobs, indent=2))
    tmp.replace(JOBS_FILE)


def jobs_load() -> list[dict]:
    with _JOBS_LOCK:
        return _jobs_load_unlocked()


def jobs_save(jobs: list[dict]) -> None:
    with _JOBS_LOCK:
        _jobs_save_unlocked(jobs)


def jobs_append(job: dict) -> None:
    with _JOBS_LOCK:
        jobs = _jobs_load_unlocked()
        jobs.insert(0, job)
        _jobs_save_unlocked(jobs)


def jobs_update(job_id: str, **fields) -> None:
    with _JOBS_LOCK:
        jobs = _jobs_load_unlocked()
        for j in jobs:
            if j.get("id") == job_id:
                j.update(fields)
                break
        _jobs_save_unlocked(jobs)


def _is_same_or_child(path: str | None, parent: str) -> bool:
    path = (path or "").strip("/")
    parent = (parent or "").strip("/")
    if not path or not parent:
        return path == parent
    return path == parent or path.startswith(f"{parent}/")


def _replace_path_prefix(path: str | None, old: str, new: str) -> str | None:
    if path is None:
        return None
    path = path.strip("/")
    old = old.strip("/")
    new = new.strip("/")
    if path == old:
        return new
    if old and path.startswith(f"{old}/"):
        suffix = path[len(old):].lstrip("/")
        return f"{new}/{suffix}" if new else suffix
    return path


def ensure_no_active_job_under(rel_path: str) -> None:
    rel_path = rel_path.strip("/")
    for job in jobs_load():
        if job.get("status") in _TERMINAL_JOB_STATUSES:
            continue
        folder = job.get("folder")
        parent = job.get("parent_path")
        if _is_same_or_child(folder, rel_path) or (folder is None and _is_same_or_child(parent, rel_path)):
            raise HTTPException(
                status_code=409,
                detail="This entry has a notetaker job in progress. Wait for it to finish before moving, renaming, or deleting it.",
            )


def jobs_repath(old: str, new: str) -> None:
    with _JOBS_LOCK:
        jobs = _jobs_load_unlocked()
        for job in jobs:
            job["folder"] = _replace_path_prefix(job.get("folder"), old, new)
            job["parent_path"] = _replace_path_prefix(job.get("parent_path"), old, new)
        _jobs_save_unlocked(jobs)


def jobs_mark_deleted(rel_path: str) -> None:
    rel_path = rel_path.strip("/")
    with _JOBS_LOCK:
        jobs = _jobs_load_unlocked()
        for job in jobs:
            folder = job.get("folder")
            if _is_same_or_child(folder, rel_path):
                job["deleted_folder"] = folder
                job["folder"] = None
        _jobs_save_unlocked(jobs)


# ---------- background pipeline ----------

def run_recall_pipeline(meeting_url: str, subject: str, parent_path: str, job_id: str) -> None:
    """Mirror of scripts/dispatch_bot.py:main, factored for BackgroundTasks."""
    try:
        folder = make_meeting_folder(subject, dt.date.today(), parent_path)
        relative = folder.resolve().relative_to(MEETINGS_ROOT.resolve())
        jobs_update(job_id, folder=str(relative))

        bot = create_bot(meeting_url, bot_name="RecallAI Notetaker")
        bot_id = bot["id"]
        jobs_update(job_id, bot_id=bot_id, status="joining")

        bot = wait_for_completion(bot_id)
        final_status = current_status(bot)
        jobs_update(job_id, status=final_status)

        if final_status in TERMINAL_FAILURE:
            jobs_update(
                job_id,
                status="failed",
                error=f"Bot ended with status {final_status}",
                finished_at=dt.datetime.now().isoformat(timespec="seconds"),
            )
            return

        turns = download_transcript_json(bot)
        text = format_transcript(turns)
        save_transcript(folder, text)

        summary = summarize(text)
        save_summary(folder, summary)

        # Auto-email if enabled at dispatch and we have recipients
        jobs = jobs_load()
        job = next((j for j in jobs if j.get("id") == job_id), {})
        recipients = job.get("recipients") or []
        if job.get("auto_email") and recipients:
            try:
                send_meeting_email(
                    recipients,
                    default_email_subject(folder.name),
                    default_email_body(folder.name, summary),
                    [folder / "transcript.txt", folder / "summary.txt"],
                )
                jobs_update(
                    job_id,
                    email_status="sent",
                    email_sent_at=dt.datetime.now().isoformat(timespec="seconds"),
                )
            except Exception as e:
                jobs_update(
                    job_id,
                    email_status="failed",
                    email_error=f"{type(e).__name__}: {e}",
                )
        elif job.get("auto_email") and not recipients:
            jobs_update(job_id, email_status="skipped_no_recipients")

        jobs_update(
            job_id,
            status="completed",
            finished_at=dt.datetime.now().isoformat(timespec="seconds"),
        )
    except Exception as exc:
        jobs_update(
            job_id,
            status="failed",
            error=f"{type(exc).__name__}: {exc}",
            finished_at=dt.datetime.now().isoformat(timespec="seconds"),
        )


# ---------- path safety ----------

def safe_path(rel_path: str) -> Path:
    """Wrap resolve_under_meetings so route handlers return 400 cleanly on bad input."""
    try:
        return resolve_under_meetings(rel_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def safe_meeting_path(full_path: str) -> Path:
    """Resolve a meeting's full path (under meetings/), 404 if it isn't a meeting."""
    path = safe_path(full_path)
    if not path.is_dir():
        raise HTTPException(status_code=404, detail="Meeting not found")
    if not is_meeting_folder(path):
        raise HTTPException(status_code=404, detail="Not a meeting folder")
    return path


def safe_parent_folder(rel_path: str) -> Path:
    """Resolve a destination folder for a new meeting."""
    path = safe_path(rel_path)
    if not path.is_dir():
        raise HTTPException(status_code=400, detail="Destination folder does not exist")
    if is_meeting_folder(path):
        raise HTTPException(status_code=400, detail="Cannot create a meeting inside another meeting")
    return path


def breadcrumbs(rel_path: str) -> list[dict]:
    """Build breadcrumb segments for a path like 'work/clients/acme'."""
    crumbs: list[dict] = []
    accum = ""
    for part in (rel_path or "").strip("/").split("/"):
        if not part:
            continue
        accum = f"{accum}/{part}" if accum else part
        crumbs.append({"name": part, "path": accum})
    return crumbs


# ---------- routes ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request, parent: str = ""):
    """Dispatch form. Optional `?parent=...` pre-selects a folder."""
    folders = list_all_folders()
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "folders": folders,
            "selected_parent": parent,
        },
    )


@app.post("/dispatch")
def dispatch(
    background_tasks: BackgroundTasks,
    meeting_url: str = Form(...),
    subject: str = Form(...),
    parent_path: str = Form(""),
    auto_email: str = Form(""),
):
    meeting_url = meeting_url.strip()
    subject = subject.strip() or "meeting"
    parent_path = parent_path.strip().strip("/")
    auto_email_enabled = auto_email.lower() in {"on", "true", "1", "yes"}

    if not MEETING_URL_RE.match(meeting_url):
        raise HTTPException(status_code=400, detail="Meeting URL must start with http:// or https://")

    safe_parent_folder(parent_path)

    # Snapshot calendar attendees at dispatch time so the auto-send has someone to send to.
    recipients: list[str] = []
    if auto_email_enabled:
        try:
            recipients = attendees_for_url(meeting_url)
        except Exception:
            recipients = []

    job = {
        "id": str(uuid.uuid4()),
        "subject": subject,
        "meeting_url": meeting_url,
        "parent_path": parent_path,
        "started_at": dt.datetime.now().isoformat(timespec="seconds"),
        "status": "starting",
        "folder": None,
        "bot_id": None,
        "finished_at": None,
        "error": None,
        "auto_email": auto_email_enabled,
        "recipients": recipients,
        "email_status": None,
        "email_sent_at": None,
        "email_error": None,
    }
    jobs_append(job)
    background_tasks.add_task(run_recall_pipeline, meeting_url, subject, parent_path, job["id"])
    return RedirectResponse(url=browse_url(parent_path), status_code=303)


def _parent_of(rel_path: str) -> str:
    return "/".join(rel_path.strip("/").split("/")[:-1])


def _back_to(parent: str) -> RedirectResponse:
    return RedirectResponse(browse_url(parent), status_code=303)


@app.post("/entries/rename")
def rename_route(path: str = Form(...), new_name: str = Form(...)):
    old_path = path.strip().strip("/")
    ensure_no_active_job_under(old_path)
    try:
        new_entry = rename_entry(old_path, new_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    new_rel = str(new_entry.resolve().relative_to(MEETINGS_ROOT.resolve()))
    jobs_repath(old_path, new_rel)
    return _back_to(_parent_of(new_rel))


@app.post("/entries/delete")
def delete_route(path: str = Form(...)):
    old_path = path.strip().strip("/")
    ensure_no_active_job_under(old_path)
    try:
        delete_entry(old_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    jobs_mark_deleted(old_path)
    return _back_to(_parent_of(old_path))


@app.post("/entries/move")
def move_route(path: str = Form(...), dest_parent: str = Form("")):
    old_path = path.strip().strip("/")
    dest_parent = dest_parent.strip().strip("/")
    ensure_no_active_job_under(old_path)
    try:
        new_entry = move_entry(old_path, dest_parent)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    new_rel = str(new_entry.resolve().relative_to(MEETINGS_ROOT.resolve()))
    jobs_repath(old_path, new_rel)
    return _back_to(dest_parent)


@app.post("/folders")
def create_folder_route(parent_path: str = Form(""), name: str = Form(...)):
    parent_path = parent_path.strip().strip("/")
    safe_parent_folder(parent_path)
    try:
        create_folder(parent_path, name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _back_to(parent_path)


@app.get("/browse", response_class=HTMLResponse)
def browse(request: Request, path: str = ""):
    path = path.strip().strip("/")
    safe_path(path)  # 400 on traversal

    folders, meeting_names = list_browse_entries(path)

    folder_entries = []
    for name in folders:
        rel = f"{path}/{name}" if path else name
        folder_entries.append({"name": name, "path": rel, "count": count_meetings_in(rel)})

    meeting_entries = []
    for name in meeting_names:
        rel = f"{path}/{name}" if path else name
        meeting_entries.append({"name": name, "path": rel})

    jobs = jobs_load()
    running = [j for j in jobs if j.get("status") not in {"completed", "failed"}]
    recent = [j for j in jobs if j.get("status") in {"completed", "failed"}][:5]

    return templates.TemplateResponse(
        "browse.html",
        {
            "request": request,
            "path": path,
            "breadcrumbs": breadcrumbs(path),
            "folders": folder_entries,
            "meetings": meeting_entries,
            "all_folders": list_all_folders(),
            "running": running if not path else [],
            "recent": recent if not path else [],
        },
    )


@app.get("/meetings", response_class=HTMLResponse)
def meetings_redirect():
    return RedirectResponse(url="/browse", status_code=307)


@app.get("/zip")
def download_zip(path: str = ""):
    """Stream a zip of everything under `path` (relative to meetings/). Works for both folders and individual meetings."""
    path = path.strip().strip("/")
    if not path:
        raise HTTPException(status_code=400, detail="Cannot zip the entire meetings root")
    target = safe_path(path)
    if not target.is_dir():
        raise HTTPException(status_code=404, detail="Not a directory")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in target.rglob("*"):
            if not entry.is_file():
                continue
            if entry.name.startswith("."):  # skip .meeting marker, .DS_Store, etc.
                continue
            arcname = entry.relative_to(target.parent)
            zf.write(entry, arcname=arcname)

    filename = f"{target.name}.zip"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# NOTE: download route MUST be declared before the detail route so the greedy
# `{full_path:path}` of the detail route doesn't swallow the /download/{filename} tail.
@app.get("/meetings/{full_path:path}/download/{filename}")
def download(full_path: str, filename: str):
    if filename not in ALLOWED_DOWNLOADS:
        raise HTTPException(status_code=400, detail="Not a downloadable file")
    path = safe_meeting_path(full_path) / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    safe_label = full_path.strip("/").replace("/", "__")
    return FileResponse(path, filename=f"{safe_label}_{filename}", media_type="application/octet-stream")


@app.post("/meetings/{full_path:path}/email")
def email_route(
    full_path: str,
    to: str = Form(...),
    subject: str = Form(...),
    body: str = Form(...),
    attach: list[str] = Form(default_factory=list),
):
    path = safe_meeting_path(full_path)

    # parse + dedupe recipients (comma, semicolon, or whitespace separated)
    raw = re.split(r"[,;\s]+", to.strip())
    recipients: list[str] = []
    seen = set()
    for r in raw:
        r = r.strip()
        if r and "@" in r and r not in seen:
            recipients.append(r)
            seen.add(r)
    if not recipients:
        return RedirectResponse(
            url=meeting_url(full_path, error="Need at least one valid email address"),
            status_code=303,
        )

    attachments: list[Path] = []
    for name in attach:
        if name in ALLOWED_DOWNLOADS:
            candidate = path / name
            if candidate.is_file():
                attachments.append(candidate)

    try:
        send_meeting_email(recipients, subject, body, attachments)
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc}"
        return RedirectResponse(url=meeting_url(full_path, error=msg), status_code=303)

    return RedirectResponse(url=meeting_url(full_path, sent=len(recipients)), status_code=303)


def _find_job_for_folder(folder_rel: str) -> dict:
    """Return the most recent job dict for a given meeting folder path, or empty dict."""
    for j in jobs_load():
        if j.get("folder") == folder_rel:
            return j
    return {}


@app.post("/meetings/{full_path:path}/qa")
def qa_route(full_path: str, question: str = Form(...)):
    """Answer a question about the meeting transcript using Claude."""
    path = safe_meeting_path(full_path)
    transcript_path = path / "transcript.txt"
    if not transcript_path.exists():
        raise HTTPException(status_code=400, detail="No transcript available for this meeting yet.")
    transcript = transcript_path.read_text()
    answer = ask(transcript, question)
    append_qa(path, question, answer)
    return JSONResponse({"question": question, "answer": answer})


@app.get("/meetings/{full_path:path}", response_class=HTMLResponse)
def meeting_detail(request: Request, full_path: str, sent: int = 0, error: str = ""):
    path = safe_meeting_path(full_path)
    transcript = (path / "transcript.txt").read_text() if (path / "transcript.txt").exists() else None
    summary = (path / "summary.txt").read_text() if (path / "summary.txt").exists() else None
    parent_rel = _parent_of(full_path)
    job = _find_job_for_folder(full_path.strip("/"))
    return templates.TemplateResponse(
        "meeting_detail.html",
        {
            "request": request,
            "full_path": full_path.strip("/"),
            "name": path.name,
            "parent_rel": parent_rel,
            "breadcrumbs": breadcrumbs(parent_rel),
            "transcript": transcript,
            "summary": summary,
            "all_folders": list_all_folders(),
            "default_email_subject": default_email_subject(path.name),
            "default_email_body": default_email_body(path.name, summary),
            "auto_email_status": job.get("email_status"),
            "auto_email_recipients": job.get("recipients", []),
            "auto_email_sent_at": job.get("email_sent_at"),
            "auto_email_error": job.get("email_error"),
            "sent_count": sent,
            "error_msg": error,
            "qa_history": load_qa(path),
        },
    )
