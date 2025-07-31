import os
import time
import uuid
import subprocess
import re
import shutil
from datetime import datetime
from PIL import Image

from db import (
    add_job,
    update_job_status,
    init_db,
    get_oldest_queued_job,
    delete_queued_jobs
)

# ==========================
# ✅ CONFIG SECTION
# ==========================
OUTPUT_DIR = os.path.expanduser("~/FluxImages")

# Virtual environments for each mode
FLUX_PYTHON = "/home/smithkt/flux_schnell_cpu/flux_env/bin/python"
SD15_PYTHON = "/home/smithkt/SD1.5/SD_env/bin/python"

# Model paths
FLUX_MODEL_PATH = "/home/smithkt/flux_schnell_cpu/flux_schnell_local"
SD15_MODEL_PATH = "/home/smithkt/SD1.5"

# ==========================
# ✅ ADD JOB TO DB & QUEUE
# ==========================
def add_job_to_db_and_queue(params):
    job_id = uuid.uuid4().hex[:8]

    # Internal filename (always random)
    internal_filename = f"{job_id}.png"

    # Optional custom filename
    requested_filename = params.get("filename")
    custom_filename = None
    if requested_filename:
        custom_filename = re.sub(r'[^a-zA-Z0-9_\-\.]', '', requested_filename)
        if not custom_filename.lower().endswith(".png"):
            custom_filename += ".png"

    # Output directory
    output_dir = params.get("output_dir")
    if output_dir:
        output_dir = os.path.abspath(os.path.expanduser(output_dir))
        os.makedirs(output_dir, exist_ok=True)
    else:
        output_dir = OUTPUT_DIR

    # Insert into DB
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
        custom_filename=custom_filename,
        init_image=params.get("init_image"),
        strength=params.get("strength")
    )

    params["job_id"] = job_id
    params["internal_filename"] = internal_filename
    params["output_dir"] = output_dir
    params["custom_filename"] = custom_filename

    return {
        "job_id": job_id,
        "status": "queued",
        "filename": internal_filename,
        "output_dir": output_dir,
        "custom_filename": requested_filename
    }

# ==========================
# ✅ CLEAR QUEUE
# ==========================
def clear_queue():
    delete_queued_jobs()

def create_thumbnail(source_path, dest_path, size=(400, 400)):
    try:
        img = Image.open(source_path)
        img.thumbnail(size)
        img.save(dest_path, "PNG", optimize=True)
        return True
    except Exception as e:
        print(f"⚠️ Failed to create thumbnail: {e}")
        return False

# ==========================
# ✅ MAIN WORKER LOOP
# ==========================
def run_worker():
    while True:
        job = get_oldest_queued_job()
        if not job:
            time.sleep(1)
            continue

        job_id = job["job_id"]
        update_job_status(job_id, "in_progress", start_time=datetime.utcnow().isoformat())

        try:
            # Make sure the user-defined output directory exists
            user_output_dir = os.path.abspath(os.path.expanduser(job.get("output_dir", OUTPUT_DIR)))
            os.makedirs(user_output_dir, exist_ok=True)
        except Exception as e:
            update_job_status(job_id, "failed", end_time=datetime.utcnow().isoformat(),
                              error_message=f"Output dir error: {e}")
            continue

        # ==========================
        # ✅ Always generate in FluxImages
        # ==========================
        internal_save_dir = OUTPUT_DIR
        internal_filename = job["filename"]
        internal_path = os.path.join(internal_save_dir, internal_filename)

        # ==========================
        # ✅ SELECT MODE & ENV
        # ==========================
        if job.get("init_image"):
            # Use SD1.5 Img2Img
            python_bin = SD15_PYTHON
            cmd = [
                python_bin,
                "/home/smithkt/flux_schnell_cpu/run_flux.py",
                "--prompt", job["prompt"],
                "--output", internal_filename,  # Always internal filename
                "--output_dir", internal_save_dir,  # Force save in FluxImages
                "--init_image", job["init_image"],
                "--strength", str(job.get("strength", 0.6)),
                "--sd_model_path", SD15_MODEL_PATH,
                "--guidance_scale", "6.5",
                "--steps", "40"  # Standard SD1.5 img2img
            ]
        else:
            # Use Flux Schnell Txt2Img
            python_bin = FLUX_PYTHON
            cmd = [
                python_bin,
                "/home/smithkt/flux_schnell_cpu/run_flux.py",
                "--prompt", job["prompt"],
                "--output", internal_filename,  # Always internal filename
                "--output_dir", internal_save_dir,  # Force save in FluxImages
                "--flux_model_path", FLUX_MODEL_PATH,
                "--steps", str(job.get("steps", 4)),
                "--guidance_scale", str(job.get("guidance_scale", 3.5)),
                "--height", str(job.get("height", 1024)),
                "--width", str(job.get("width", 1024))
            ]

        if job.get("autotune"):
            cmd.append("--autotune")

        # ==========================
        # ✅ EXECUTE JOB
        # ==========================
        try:
            subprocess.run(cmd, check=True)

            # ✅ Copy/rename if needed
            try:
                dest_dir = user_output_dir
                custom_filename = job.get("custom_filename")

                if custom_filename:
                    dest_path = os.path.join(dest_dir,
                                             custom_filename if custom_filename.endswith(".png") else custom_filename + ".png")
                    shutil.copy2(internal_path, dest_path)
                    print(f"✅ Copied and renamed to: {dest_path}")
                elif os.path.abspath(dest_dir) != os.path.abspath(internal_save_dir):
                    dest_path = os.path.join(dest_dir, internal_filename)
                    shutil.copy2(internal_path, dest_path)
                    print(f"✅ Copied to: {dest_path}")
            except Exception as copy_err:
                print(f"⚠️ Failed to copy to output_dir: {copy_err}")

            update_job_status(job_id, "done", end_time=datetime.utcnow().isoformat())

        except subprocess.CalledProcessError as e:
            update_job_status(job_id, "failed", end_time=datetime.utcnow().isoformat(),
                              error_message=f"Subprocess error: {e}")
        except Exception as e:
            update_job_status(job_id, "failed", end_time=datetime.utcnow().isoformat(),
                              error_message=f"Unexpected error: {e}")

# Init DB
init_db()
print("✅ Job queue initialized.")
