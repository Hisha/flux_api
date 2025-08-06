from dotenv import load_dotenv
load_dotenv()

import os
import sqlite3
import multiprocessing
import random
from fastapi import FastAPI, HTTPException, Query, Request, Form, status, Header, Depends, APIRouter, Body, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.responses import Response
from starlette.middleware.sessions import SessionMiddleware
from auth import verify_password, require_login, is_authenticated
from db import add_job, get_job, get_job_by_filename, get_job_metrics, get_recent_jobs, delete_old_jobs, get_completed_jobs_for_archive, delete_job, get_all_jobs, get_oldest_queued_job, count_jobs_by_status
from job_queue import add_job_to_db_and_queue, clear_queue
from typing import Optional
from datetime import datetime
import uuid
import pytz
from dateutil import parser
import logging
import shutil
import psutil

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(root_path="/flux")
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY"))
OUTPUT_DIR = os.path.expanduser("~/FluxImages")
UPLOAD_DIR = os.path.join(OUTPUT_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
templates = Jinja2Templates(directory="templates")
templates.env.globals["root_path"] = "/flux"
templates.env.globals['now'] = datetime.now
API_TOKEN = os.getenv("N8N_API_TOKEN")
eastern = pytz.timezone("US/Eastern")
LINKABLE_DIR = "/mnt/ai_data/linkable"

# Add custom Jinja filter for basename
templates.env.filters["basename"] = lambda path: os.path.basename(path) if path else ""

class PromptRequest(BaseModel):
    prompt: str
    steps: int = 4
    guidance_scale: float = 3.5
    height: int = 1024
    width: int = 1024
    autotune: bool = True
    filename: Optional[str] = None  # Optional custom filename
    output_dir: Optional[str] = None
    init_image: Optional[str] = None   # img2img
    strength: float = 0.75    #img2img         

def format_local_time(iso_str):
    try:
        utc_time = parser.isoparse(iso_str)
        local_time = utc_time.astimezone(eastern)
        return local_time.strftime("%Y-%m-%d %I:%M %p %Z")  # e.g., 2025-07-15 02:30 PM EDT
    except Exception:
        return iso_str  # fallback

# ✅ Register it as a Jinja2 filter
templates.env.filters["localtime"] = format_local_time

def parse_time(ts):
    if not ts:
        return datetime.utcnow()  # instead of datetime.min
    try:
        return datetime.fromisoformat(ts)
    except:
        return datetime.utcnow()  # fallback to now if invalid

def require_token(authorization: str = Header(None), request: Request = None):
    expected_token = os.getenv("N8N_API_TOKEN")

    token = None
    if authorization and authorization.startswith("Bearer "):
        token = authorization[len("Bearer "):].strip()

    if token != expected_token and not is_authenticated(request):
        raise HTTPException(status_code=403, detail="Unauthorized")

def sort_job_priority(job):
    priority = {
        "processing": 1,
        "in_progress": 1,
        "queued": 2,
        "failed": 3,
        "done": 4
    }
    return (
        -priority.get(job["status"], 0),  # High priority first
        parse_time(job.get("end_time") or job.get("start_time")),  # Most recent first
    )

#####################################################################################
#                                   GET                                             #
#####################################################################################

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    # Pull a larger pool, sort by priority, then cut to 50
    jobs = get_recent_jobs(limit=50)

    metrics = get_job_metrics()
    return templates.TemplateResponse("index.html", {
        "request": request,
        "jobs": jobs,
        "metrics": metrics
    })

@app.get("/admin", response_class=HTMLResponse)
def admin_panel(request: Request):
    require_login(request)
    system = admin_system_info(request)
    metrics = get_job_metrics()
    try:
        linkable_files = []
        for f in os.listdir(LINKABLE_DIR):
            full_path = os.path.join(LINKABLE_DIR, f)
            if os.path.isfile(full_path):
                mtime = os.path.getmtime(full_path)
                linkable_files.append((f, format_local_time(datetime.utcfromtimestamp(mtime).isoformat())))
        # Sort by most recent modified time
        linkable_files.sort(key=lambda x: x[1], reverse=True)
    except Exception:
        linkable_files = []

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "system": system,
        "metrics": metrics,
        "linkable_files": linkable_files
    })

@app.get("/admin/metrics")
def metrics(request: Request):
    require_login(request)
    return get_job_metrics()

@app.get("/admin/system")
def admin_system_info(request: Request):
    require_login(request)

    # Disk usage
    disk = shutil.disk_usage(os.path.expanduser("~/FluxImages"))
    disk_total = round(disk.total / (1024**3), 1)  # GB
    disk_used = round(disk.used / (1024**3), 1)
    disk_free = round(disk.free / (1024**3), 1)

    # RAM usage
    mem = psutil.virtual_memory()
    memory_total = round(mem.total / (1024**3), 1)
    memory_used = round(mem.used / (1024**3), 1)
    memory_percent = mem.percent

    # Job/queue info
    active_queue = count_jobs_by_status("queued")
    active_workers = count_jobs_by_status("in_progress")

    return {
        "cpu_cores": multiprocessing.cpu_count(),
        "output_dir": OUTPUT_DIR,
        "active_queue_length": active_queue,
        "active_workers": active_workers,
        "disk_total_gb": disk_total,
        "disk_used_gb": disk_used,
        "disk_free_gb": disk_free,
        "memory_total_gb": memory_total,
        "memory_used_gb": memory_used,
        "memory_percent": memory_percent
    }

import random
from fastapi import Query

@app.get("/gallery", response_class=HTMLResponse)
def gallery(request: Request, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100)):
    image_dir = os.path.expanduser("~/FluxImages")
    files = [f for f in os.listdir(image_dir) if f.lower().endswith(".png")]
    random.shuffle(files)

    # Calculate pagination bounds
    total = len(files)
    start = (page - 1) * limit
    end = start + limit
    page_files = files[start:end]

    images = []
    for fname in page_files:
        job = get_job_by_filename(fname)
        if job:
            images.append({"filename": fname, "job_id": job["job_id"]})

    return templates.TemplateResponse("gallery.html", {
        "request": request,
        "images": images,
        "page": page,
        "limit": limit,
        "total": total,
        "has_prev": page > 1,
        "has_next": end < total,
        "root_path": request.scope.get("root_path", "")
    })

@app.get("/gallery/{job_id}", response_class=HTMLResponse)
def view_gallery(request: Request, job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return templates.TemplateResponse("gallery_detail.html", {
        "request": request,
        "job": job
    })

@app.get("/gallery/json")
def gallery_json(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    sort: str = Query("random", regex="^(random|newest)$")  # random or newest
):
    image_dir = os.path.expanduser("~/FluxImages")
    files = [f for f in os.listdir(image_dir) if f.lower().endswith(".png")]

    if sort == "random":
        # ✅ Assign seed if not present
        if "gallery_seed" not in request.session:
            request.session["gallery_seed"] = random.randint(1, 1_000_000)

        seeded_random = random.Random(request.session["gallery_seed"])
        seeded_random.shuffle(files)
    else:
        # ✅ Newest first (sorted by modified time)
        files.sort(key=lambda f: os.path.getmtime(os.path.join(image_dir, f)), reverse=True)

    # Pagination
    total = len(files)
    start = (page - 1) * limit
    end = start + limit
    page_files = files[start:end]

    images = []
    for fname in page_files:
        job = get_job_by_filename(fname)
        if job:
            images.append({
                "filename": fname,
                "job_id": job["job_id"],
                "thumbnail_url": f"/flux/thumbnails/{fname}",
                "detail_url": f"/flux/gallery/{job['job_id']}"
            })

    return {
        "images": images,
        "has_next": end < total
    }

@app.get("/images/{filename}")
def get_image(filename: str):
    image_path = os.path.join(OUTPUT_DIR, filename)

    if not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail="Image not found in FluxImages")

    return FileResponse(image_path, media_type="image/png")

@app.get("/jobs/json")
def jobs_json(status: str = Query(None), limit: int = Query(50)):
    jobs = get_recent_jobs(limit=limit, status=status)
    jobs = sorted(jobs, key=sort_job_priority, reverse=True)
    return jobs
    
@app.get("/jobs", response_class=HTMLResponse)
async def job_dashboard(
    request: Request,
    status: str = Query("all"),
    q: str = Query("")
):
    require_login(request)
    jobs = get_recent_jobs(status=status)
    if q:
        jobs = [j for j in jobs if q.lower() in j["prompt"].lower()]
    jobs = sorted(jobs, key=sort_job_priority, reverse=True)

    # ✅ Fetch all gallery images sorted by filename
    image_dir = os.path.expanduser("~/FluxImages")
    gallery_images = [f for f in os.listdir(image_dir) if f.lower().endswith(".png")]
    gallery_images.sort()  # Sort alphabetically; use reverse=True for reverse order

    return templates.TemplateResponse("jobs.html", {
        "request": request,
        "jobs": jobs,
        "gallery_images": gallery_images,  # Pass to template
        "status_filter": status,
        "search_query": q
    })

@app.get("/jobs/{job_id}")
def job_details(request: Request, job_id: str):
    require_login(request)
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.get("/job/{job_id}", response_class=HTMLResponse)
def view_job(request: Request, job_id: str):
    require_login(request)
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return templates.TemplateResponse("job_detail.html", {
        "request": request,
        "job": job
    })

@app.get("/linkable", response_class=HTMLResponse)
def linkable_page(request: Request):
    
    try:
        files = [
            f for f in os.listdir(LINKABLE_DIR)
            if os.path.isfile(os.path.join(LINKABLE_DIR, f))
        ]
    except Exception:
        files = []
    return templates.TemplateResponse("linkable.html", {
        "request": request,
        "files": files
    })

@app.get("/linkable/download/{filename}")
def download_linkable_file(filename: str):
    # Sanitize filename and enforce safe path
    safe_path = os.path.abspath(os.path.join(LINKABLE_DIR, filename))
    if not safe_path.startswith(os.path.abspath(LINKABLE_DIR)) or not os.path.isfile(safe_path):
        raise HTTPException(status_code=404, detail="Invalid file")
    return FileResponse(safe_path, filename=filename, media_type="application/octet-stream")

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})

@app.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/flux/login", status_code=303)

@app.get("/metrics/json")
def metrics_json():
    return get_job_metrics()

@app.get("/partials/job_table", response_class=HTMLResponse)
async def partial_job_table(
    request: Request,
    status: str = Query("all"),
    q: str = Query("")
):
    jobs = get_recent_jobs(status=status)
    if q:
        jobs = [j for j in jobs if q.lower() in j["prompt"].lower()]
    jobs = sorted(jobs, key=sort_job_priority, reverse=True)

    return templates.TemplateResponse("partials/_job_table.html", {
        "request": request,
        "jobs": jobs,
        "status_filter": status,
        "search_query": q
    })

@app.get("/partials/metrics", response_class=HTMLResponse)
def partial_metrics(request: Request):
    metrics = get_job_metrics()
    return templates.TemplateResponse("partials/_metrics.html", {"request": request, "metrics": metrics})

@app.get("/partials/recent_jobs", response_class=HTMLResponse)
def partial_recent_jobs(request: Request):
    jobs = get_recent_jobs(limit=50)
    return templates.TemplateResponse("partials/_recent_jobs.html", {
        "request": request,
        "jobs": jobs
    })

@app.get("/privacy", response_class=HTMLResponse)
def privacy_page(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})

@app.get("/status/{job_id}")
def status(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@app.get("/terms", response_class=HTMLResponse)
def terms_page(request: Request):
    return templates.TemplateResponse("terms.html", {"request": request})

@app.get("/thumbnails/{filename}")
def get_thumbnail(filename: str):
    thumb_path = os.path.join(OUTPUT_DIR, "thumbnails", filename)
    if not os.path.exists(thumb_path):
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    headers = {
        "Cache-Control": "public, max-age=31536000, immutable"  # 1 year cache
    }
    return FileResponse(thumb_path, media_type="image/png", headers=headers)

#####################################################################################
#                                   POST                                            #
#####################################################################################

@app.post("/admin/archive")
def archive_images(request: Request, days: int = 1):
    require_login(request)
    jobs = get_completed_jobs_for_archive(days)
    archived = []
    for job in jobs:
        try:
            archive_date = job["end_time"].split("T")[0]
            archive_dir = os.path.join(OUTPUT_DIR, "archive", archive_date)
            os.makedirs(archive_dir, exist_ok=True)
            src = os.path.join(OUTPUT_DIR, job["filename"])
            dst = os.path.join(archive_dir, job["filename"])
            if os.path.exists(src):
                os.rename(src, dst)
                archived.append(dst)
        except Exception:
            pass
    return RedirectResponse(url=f"{request.scope.get('root_path', '')}/admin", status_code=303)

@app.post("/admin/archive_done")
def archive_done(request: Request):
    require_login(request)
    conn = sqlite3.connect(os.path.expanduser("~/flux_api/flux_jobs.db"))
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS archived_jobs AS SELECT * FROM jobs WHERE 0''')
    c.execute("INSERT INTO archived_jobs SELECT * FROM jobs WHERE status = 'done'")
    c.execute("DELETE FROM jobs WHERE status = 'done'")
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"{request.scope.get('root_path', '')}/admin", status_code=303)

@app.post("/admin/cleanup")
def cleanup(request: Request, days: int = 7):
    require_login(request)
    deleted = delete_old_jobs(days=days)
    deleted_files = []
    for job in deleted:
        filepath = os.path.join(OUTPUT_DIR, job["filename"])
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
                deleted_files.append(job["filename"])
            except Exception:
                pass
    return RedirectResponse(url=f"{request.scope.get('root_path', '')}/admin", status_code=303)

@app.post("/admin/cleanup_failed")
def cleanup_failed(request: Request):
    require_login(request)
    conn = sqlite3.connect(os.path.expanduser("~/flux_api/flux_jobs.db"))
    c = conn.cursor()
    c.execute("SELECT filename FROM jobs WHERE status = 'failed'")
    for row in c.fetchall():
        f = os.path.join(OUTPUT_DIR, row[0])
        if os.path.exists(f):
            os.remove(f)
    c.execute("DELETE FROM jobs WHERE status = 'failed'")
    conn.commit()
    conn.close()
    return RedirectResponse(url=f"{request.scope.get('root_path', '')}/admin", status_code=303)

@app.post("/admin/clear_queue")
def admin_clear_queue(request: Request):
    require_login(request)
    clear_queue()
    return RedirectResponse(url=f"{request.scope.get('root_path', '')}/admin", status_code=303)

@app.post("/admin/delete/{job_id}")
def admin_delete(request: Request, job_id: str):
    require_login(request)
    filename = delete_job(job_id)
    if not filename:
        raise HTTPException(status_code=404, detail="Job not found")
    path = os.path.join(OUTPUT_DIR, filename)
    if os.path.exists(path):
        os.remove(path)
    return RedirectResponse(url="/flux/jobs", status_code=303)

@app.post("/clear_queue")
def clear_queue_api(auth=Depends(require_token)):
    clear_queue()
    return {"message": "Queue cleared successfully."}

@app.post("/generate", response_class=HTMLResponse)
def generate_from_form(
    request: Request,
    prompt: str = Form(...),
    steps: int = Form(4),
    guidance_scale: float = Form(3.5),
    height: int = Form(1024),
    width: int = Form(1024),
    filename: Optional[str] = Form(None),
    strength: float = Form(0.75),                # img2img
    init_image: UploadFile = File(None),         # img2img upload
    gallery_image: Optional[str] = Form(None)    # img2img from gallery
):
    require_login(request)
    init_image_path = None

    # ✅ Case 1: Uploaded image
    if init_image and init_image.filename:
        suffix = os.path.splitext(init_image.filename)[-1] or ".png"
        init_image_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}{suffix}")

        # Save uploaded image
        with open(init_image_path, "wb") as buffer:
            shutil.copyfileobj(init_image.file, buffer)

        # Validate size (avoid zero-byte files)
        if os.path.getsize(init_image_path) == 0:
            os.remove(init_image_path)
            raise HTTPException(status_code=400, detail="Uploaded image is empty")

        # Copy to gallery
        gallery_copy = os.path.join(OUTPUT_DIR, f"init_{uuid.uuid4().hex}{suffix}")
        shutil.copy2(init_image_path, gallery_copy)
        logger.info(f"Uploaded init image saved as {gallery_copy}")

    # ✅ Case 2: Gallery image selected
    elif gallery_image:
        candidate_path = os.path.join(OUTPUT_DIR, gallery_image)
        if os.path.exists(candidate_path):
            init_image_path = candidate_path
        else:
            raise HTTPException(status_code=404, detail="Selected gallery image not found")

    # ✅ Case 3: Neither provided → leave init_image_path as None
    job_info = add_job_to_db_and_queue({
        "prompt": prompt.strip(),
        "steps": steps,
        "guidance_scale": guidance_scale,
        "height": height,
        "width": width,
        "filename": filename,
        "autotune": True,  # Force autotune always
        "init_image": init_image_path,
        "strength": strength
    })

    return RedirectResponse(url=f"{request.scope.get('root_path', '')}/job/{job_info['job_id']}", status_code=303)

@app.post("/generate/json")
def generate_from_json(payload: PromptRequest, request: Request, auth=Depends(require_token)):
    payload.prompt = payload.prompt.strip()

    # ✅ Only process img2img validation if init_image is provided
    if payload.init_image:
        candidate = os.path.join(OUTPUT_DIR, payload.init_image)

        if not os.path.exists(payload.init_image) and not os.path.exists(candidate):
            raise HTTPException(status_code=404, detail="Init image not found")

        # If the original path doesn't exist, use candidate
        payload.init_image = payload.init_image if os.path.exists(payload.init_image) else candidate
    else:
        # If init_image is None, it's a txt2img request, leave it alone
        payload.init_image = None

    job_info = add_job_to_db_and_queue(payload.dict())
    return {
        "message": "Job submitted successfully",
        "job_id": job_info["job_id"],
        "filename": job_info["filename"]
    }

@app.post("/jobs/{job_id}/retry")
def retry_job(request: Request, job_id: str):
    require_login(request)
    original = get_job_for_retry(job_id)
    if not original:
        raise HTTPException(status_code=400, detail="Job not found or not failed")

    new_id = uuid.uuid4().hex[:8]
    new_filename = f"{new_id}.png"

    add_job(
        job_id=new_id,
        prompt=original["prompt"],
        steps=original["steps"],
        guidance_scale=original["guidance_scale"],
        height=original["height"],
        width=original["width"],
        autotune=bool(original["autotune"]),
        filename=new_filename,
        output_dir=original.get("output_dir", os.path.expanduser("~/FluxImages"))
    )

    return RedirectResponse(url=f"{request.scope.get('root_path', '')}/admin", status_code=303)

@app.post("/linkable/delete/{filename}")
def delete_linkable_file(request: Request, filename: str):
    require_login(request)
    safe_path = os.path.abspath(os.path.join(LINKABLE_DIR, filename))
    if not safe_path.startswith(os.path.abspath(LINKABLE_DIR)) or not os.path.isfile(safe_path):
        raise HTTPException(status_code=404, detail="Invalid file path")
    try:
        os.remove(safe_path)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to delete file")
    return RedirectResponse(url=f"{request.scope.get('root_path', '')}/admin", status_code=303)

@app.post("/login", response_class=HTMLResponse)
def login_submit(request: Request, password: str = Form(...)):
    if verify_password(password):
        request.session["logged_in"] = True
        return RedirectResponse(url="/flux", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid password"})
