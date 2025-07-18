import os
import time
import uuid
import subprocess
import re
import shutil
from datetime import datetime

from db import (
    add_job,
    update_job_status,
    init_db,
    get_oldest_queued_job,
    delete_queued_jobs
)

OUTPUT_DIR = os.path.expanduser("~/FluxImages")

def add_job_to_db_and_queue(params):
    job_id = uuid.uuid4().hex[:8]

    # Always use random filename internally
    internal_filename = f"{job_id}.png"

    # If a custom filename is requested for external use
    requested_filename = params.get("filename")
    custom_filename = None
    if requested_filename:
        custom_filename = re.sub(r'[^a-zA-Z0-9_\-\.]', '', requested_filename)
        if not custom_filename.lower().endswith(".png"):
            custom_filename += ".png"

    # Handle output_dir override
    output_dir = params.get("output_dir")
    if output_dir:
        output_dir = os.path.abspath(os.path.expanduser(output_dir))
        os.makedirs(output_dir, exist_ok=True)
    else:
        output_dir = OUTPUT_DIR

    # Write to DB (status = queued)
    add_job(
        job_id=job_id,
        prompt=params["prompt"],
        steps=params.get("steps", 4),
        guidance_scale=params.get("guidance_scale", 3.5),
        height=params.get("height", 1024),
        width=params.get("width", 1024),
        autotune=params.get("autotune", True),
        filename=internal_filename,
        output_dir=output_dir,
        custom_filename=custom_filename  # <-- ensures it lands in DB
    )

    return {
        "job_id": job_id,
        "status": "queued",
        "filename": internal_filename,
        "output_dir": output_dir,
        "custom_filename": requested_filename  # Optional info for downstream
    }

def clear_queue():
    delete_queued_jobs()

def run_worker():
    while True:
        job = get_oldest_queued_job()
        if not job:
            time.sleep(1)
            continue

        job_id = job["job_id"]
        update_job_status(job_id, "in_progress", start_time=datetime.utcnow().isoformat())

        try:
            output_dir = os.path.abspath(os.path.expanduser(job.get("output_dir", OUTPUT_DIR)))
            os.makedirs(output_dir, exist_ok=True)
        except Exception as e:
            update_job_status(job_id, "failed", end_time=datetime.utcnow().isoformat(), error_message=f"Output dir error: {e}")
            continue

        cmd = [
            "/home/smithkt/flux_schnell_cpu/flux_env/bin/python",
            "/home/smithkt/flux_schnell_cpu/run_flux.py",
            "--prompt", job["prompt"],
            "--output", job["filename"],
            "--steps", str(job["steps"]),
            "--guidance_scale", str(job["guidance_scale"]),
            "--height", str(job["height"]),
            "--width", str(job["width"]),
            "--output_dir", OUTPUT_DIR
        ]
        if job.get("autotune"):
            cmd.append("--autotune")

        try:
            subprocess.run(cmd, check=True)

            
            # Check if we need to copy to a custom output_dir with a custom filename
            original_path = os.path.join(OUTPUT_DIR, job["filename"])
            try:
               dest_dir = job.get("output_dir", OUTPUT_DIR)
               custom_filename = job.get("custom_filename")

               if custom_filename:
                   # Sanitize and ensure .png extension
                   custom_filename = re.sub(r'[^a-zA-Z0-9_\-\.]', '', custom_filename)
                   if not custom_filename.lower().endswith(".png"):
                       custom_filename += ".png"
                   dest_path = os.path.join(dest_dir, custom_filename)

                   # Copy with rename
                   shutil.copy2(original_path, dest_path)
                   print(f"✅ Copied and renamed to: {dest_path}")
               elif os.path.abspath(dest_dir) != os.path.abspath(OUTPUT_DIR):
                   # Just copy with same filename if no rename
                   dest_path = os.path.join(dest_dir, job["filename"])
                   shutil.copy2(original_path, dest_path)
                   print(f"✅ Copied to: {dest_path}")
            except Exception as copy_err:
               print(f"⚠️ Failed to copy to output_dir: {copy_err}")


            update_job_status(job_id, "done", end_time=datetime.utcnow().isoformat())

        except Exception as e:
            update_job_status(job_id, "failed", end_time=datetime.utcnow().isoformat(), error_message=str(e))

# Init DB
init_db()
print("✅ Job queue initialized.")
