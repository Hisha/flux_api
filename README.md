# ⚡ Flux API

**Flux API** is a self-hosted image generation dashboard built with **FastAPI**, **Jinja2**, and **SQLite**. It provides a lightweight web interface for managing local AI image generation jobs, monitoring system activity, viewing generated content, and securely sharing files.

---

## 🌟 Features

- 🧠 **Job Submission UI**  
  Submit prompts and generation parameters easily from a clean interface.

- 🖼️ **Image Gallery**  
  View, paginate, and browse all generated images from a central place.

- 📊 **Metrics Dashboard**  
  Monitor job stats like total submissions, success/failure rate, and duration averages.

- 🛠️ **Admin Tools**  
  Clean up failed/done jobs, archive completed outputs, and manage the job queue.

- 🔗 **Linkable File Server**  
  Drop files into a folder and make them accessible for download from a dedicated page.

- 🔁 **n8n-Compatible API**  
  Submit jobs or clear the queue programmatically using authenticated API calls.

---

## 🚀 Getting Started

### 1. Clone the Repository

```bash
git clone https://github.com/Hisha/flux_api.git
cd flux_api
2. Configure Environment
Create a .env file in the project root:

ini
Copy
Edit
SECRET_KEY=your-secret-key
N8N_API_TOKEN=your-api-token
3. Install Dependencies
Python 3.10+ is recommended.

bash
Copy
Edit
pip install -r requirements.txt
4. Run the Server
bash
Copy
Edit
uvicorn flux_api:app --reload
Then visit: http://localhost:8000/flux

🔐 Authentication
The Admin Panel and Job Dashboard require login.

API access requires a Bearer token (N8N_API_TOKEN) passed in the Authorization header.

The password is defined inside auth.py.

📁 Folder Structure
Path	Purpose
templates/	Jinja2 HTML templates
~/FluxImages/	All generated images are saved here
/mnt/ai_data/linkable/	Shared folder for downloadable linkable files
flux_jobs.db	SQLite database for job queue and history

📡 API Endpoints
All token-protected endpoints require an Authorization: Bearer <token> header.

POST /flux/generate/json – Submit a new image job (via JSON)

GET /flux/jobs/json – Get recent jobs as JSON

POST /flux/clear_queue – Clear all queued jobs

🖼️ Screenshots
(Add screenshots here of dashboard, gallery, and admin page once available)

📌 Future Plans
 Add image tags and search filters

 Job retry or edit from UI

 Dark/light mode toggle

 Gallery upload integration (e.g., Telegram or YouTube Shorts)

👤 Maintainer
Kevin Smith
GitHub: @Hisha

This project is part of the larger Fantasy Broadcast Network AI content automation system.

🛡️ License
This project is licensed under the MIT License.
Free to use, modify, and distribute.
