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
import uuid
import zipfile
from pathlib import Path

from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
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
from src.recall_client import (
    TERMINAL_FAILURE,
    create_bot,
    current_status,
    download_transcript_json,
    format_transcript,
    wait_for_completion,
)
from src.summarizer import summarize

load_dotenv()

app = FastAPI(title="RecallAI")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

JOBS_FILE = MEETINGS_ROOT / "_jobs.json"
ALLOWED_DOWNLOADS = {"transcript.txt", "summary.txt"}
MEETING_URL_RE = re.compile(r"^https?://", re.IGNORECASE)


# ---------- job log ----------

def jobs_load() -> list[dict]:
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


def jobs_save(jobs: list[dict]) -> None:
    MEETINGS_ROOT.mkdir(parents=True, exist_ok=True)
    JOBS_FILE.write_text(json.dumps(jobs, indent=2))


def jobs_append(job: dict) -> None:
    jobs = jobs_load()
    jobs.insert(0, job)
    jobs_save(jobs)


def jobs_update(job_id: str, **fields) -> None:
    jobs = jobs_load()
    for j in jobs:
        if j.get("id") == job_id:
            j.update(fields)
            break
    jobs_save(jobs)


# ---------- background pipeline ----------

def run_recall_pipeline(meeting_url: str, subject: str, parent_path: str, job_id: str) -> None:
    """Mirror of scripts/dispatch_bot.py:main, factored for BackgroundTasks."""
    try:
        folder = make_meeting_folder(subject, dt.date.today(), parent_path)
        rel_folder = folder.relative_to(Path.cwd()) if folder.is_absolute() and str(folder).startswith(str(Path.cwd())) else folder
        # store path relative to MEETINGS_ROOT for clean URLs
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
):
    meeting_url = meeting_url.strip()
    subject = subject.strip() or "meeting"
    parent_path = parent_path.strip().strip("/")

    if not MEETING_URL_RE.match(meeting_url):
        raise HTTPException(status_code=400, detail="Meeting URL must start with http:// or https://")

    # validate parent_path early (so we don't accept a traversal here)
    if parent_path:
        safe_path(parent_path)

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
    }
    jobs_append(job)
    background_tasks.add_task(run_recall_pipeline, meeting_url, subject, parent_path, job["id"])
    redirect_target = f"/browse?path={parent_path}" if parent_path else "/browse"
    return RedirectResponse(url=redirect_target, status_code=303)


def _parent_of(rel_path: str) -> str:
    return "/".join(rel_path.strip("/").split("/")[:-1])


def _back_to(parent: str) -> RedirectResponse:
    return RedirectResponse(f"/browse?path={parent}" if parent else "/browse", status_code=303)


@app.post("/entries/rename")
def rename_route(path: str = Form(...), new_name: str = Form(...)):
    try:
        rename_entry(path, new_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _back_to(_parent_of(path))


@app.post("/entries/delete")
def delete_route(path: str = Form(...)):
    try:
        delete_entry(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _back_to(_parent_of(path))


@app.post("/entries/move")
def move_route(path: str = Form(...), dest_parent: str = Form("")):
    try:
        move_entry(path, dest_parent)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return _back_to(dest_parent.strip().strip("/"))


@app.post("/folders")
def create_folder_route(parent_path: str = Form(""), name: str = Form(...)):
    parent_path = parent_path.strip().strip("/")
    if parent_path:
        safe_path(parent_path)
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


@app.get("/meetings/{full_path:path}", response_class=HTMLResponse)
def meeting_detail(request: Request, full_path: str):
    path = safe_meeting_path(full_path)
    transcript = (path / "transcript.txt").read_text() if (path / "transcript.txt").exists() else None
    summary = (path / "summary.txt").read_text() if (path / "summary.txt").exists() else None
    parent_rel = _parent_of(full_path)
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
        },
    )
