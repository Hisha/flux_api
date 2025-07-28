from dotenv import load_dotenv
load_dotenv()

import os
import sqlite3
import multiprocessing
import random
import shutil
import tempfile
import uuid
from datetime import datetime
import pytz
from dateutil import parser
import logging
import psutil
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request, Form, Header, Depends, File, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

from auth import verify_password, require_login, is_authenticated
from db import add_job, get_job, get_job_by_filename, get_job_metrics, get_recent_jobs, delete_old_jobs, \
    get_completed_jobs_for_archive, delete_job, get_all_jobs, get_oldest_queued_job, count_jobs_by_status
from job_queue import add_job_to_db_and_queue, clear_queue

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


# ✅ Request Model
class PromptRequest(BaseModel):
    prompt: str
    steps: int = 4
    guidance_scale: float = 3.5
    height: int = 1024
    width: int = 1024
    autotune: bool = True
    filename: Optional[str] = None
    output_dir: Optional[str] = None
    init_image: Optional[str] = None   # Path or gallery image
    strength: float = 0.75


# ✅ Utility: Format time for UI
def format_local_time(iso_str):
    try:
        utc_time = parser.isoparse(iso_str)
        local_time = utc_time.astimezone(eastern)
        return local_time.strftime("%Y-%m-%d %I:%M %p %Z")
    except Exception:
        return iso_str


# Register Jinja filter
templates.env.filters["localtime"] = format_local_time


def parse_time(ts):
    if not ts:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(ts)
    except:
        return datetime.utcnow()


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
    return (-priority.get(job["status"], 0), parse_time(job.get("end_time") or job.get("start_time")))


#####################################################################################
#                                   GET                                             #
#####################################################################################

@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    jobs = get_recent_jobs(limit=50)
    metrics = get_job_metrics()
    return templates.TemplateResponse("index.html", {"request": request, "jobs": jobs, "metrics": metrics})


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
        linkable_files.sort(key=lambda x: x[1], reverse=True)
    except Exception:
        linkable_files = []
    return templates.TemplateResponse("admin.html", {"request": request, "system": system, "metrics": metrics, "linkable_files": linkable_files})


@app.get("/admin/system")
def admin_system_info(request: Request):
    require_login(request)
    disk = shutil.disk_usage(os.path.expanduser("~/FluxImages"))
    mem = psutil.virtual_memory()
    return {
        "cpu_cores": multiprocessing.cpu_count(),
        "output_dir": OUTPUT_DIR,
        "active_queue_length": count_jobs_by_status("queued"),
        "active_workers": count_jobs_by_status("in_progress"),
        "disk_total_gb": round(disk.total / (1024**3), 1),
        "disk_used_gb": round(disk.used / (1024**3), 1),
        "disk_free_gb": round(disk.free / (1024**3), 1),
        "memory_total_gb": round(mem.total / (1024**3), 1),
        "memory_used_gb": round(mem.used / (1024**3), 1),
        "memory_percent": mem.percent
    }


@app.get("/images/{filename}")
def get_image(filename: str):
    image_path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(image_path):
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(image_path, media_type="image/png")


#####################################################################################
#                                   POST                                            #
#####################################################################################

@app.post("/generate", response_class=HTMLResponse)
async def generate_from_form(
    request: Request,
    prompt: str = Form(...),
    steps: int = Form(4),
    guidance_scale: float = Form(3.5),
    height: int = Form(1024),
    width: int = Form(1024),
    strength: float = Form(0.75),
    filename: Optional[str] = Form(None),
    init_image: UploadFile = File(None),
    gallery_image: Optional[str] = Form(None)
):
    require_login(request)
    init_image_path = None

    if init_image:
        suffix = os.path.splitext(init_image.filename)[-1] or ".png"
        init_image_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}{suffix}")
        with open(init_image_path, "wb") as buffer:
            shutil.copyfileobj(init_image.file, buffer)
        # Copy to gallery as well for reuse
        gallery_copy = os.path.join(OUTPUT_DIR, f"init_{uuid.uuid4().hex}{suffix}")
        shutil.copy2(init_image_path, gallery_copy)
        logger.info(f"Uploaded init image saved as {gallery_copy}")
    elif gallery_image:
        # Validate gallery image exists
        candidate = os.path.join(OUTPUT_DIR, gallery_image)
        if os.path.exists(candidate):
            init_image_path = candidate
        else:
            raise HTTPException(status_code=404, detail="Gallery image not found")

    job_info = add_job_to_db_and_queue({
        "prompt": prompt.strip(),
        "steps": steps,
        "guidance_scale": guidance_scale,
        "height": height,
        "width": width,
        "filename": filename,
        "autotune": True,
        "init_image": init_image_path,
        "strength": strength
    })

    return RedirectResponse(url=f"{request.scope.get('root_path', '')}/job/{job_info['job_id']}", status_code=303)


@app.post("/generate/json")
def generate_from_json(payload: PromptRequest, request: Request, auth=Depends(require_token)):
    payload.prompt = payload.prompt.strip()
    # If init_image provided, ensure it exists in gallery or path
    if payload.init_image:
        candidate = os.path.join(OUTPUT_DIR, payload.init_image)
        if not os.path.exists(payload.init_image) and not os.path.exists(candidate):
            raise HTTPException(status_code=404, detail="Init image not found")
        payload.init_image = payload.init_image if os.path.exists(payload.init_image) else candidate

    job_info = add_job_to_db_and_queue(payload.dict())
    return {"message": "Job submitted successfully", "job_id": job_info["job_id"], "filename": job_info["filename"]}
