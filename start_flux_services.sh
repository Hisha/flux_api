#!/bin/bash
source /home/smithkt/flux_schnell_cpu/flux_env/bin/activate
cd /home/smithkt/flux_api

# Start FastAPI server in background
/home/smithkt/flux_schnell_cpu/flux_env/bin/uvicorn flux_api:app --host 0.0.0.0 --port 8000 &

# Start worker pool (waits)
python start_workers.py
